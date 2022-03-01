import asyncio
from middlewared.utils import run, osc

import os
import re
import textwrap
import uuid


SYSTEM_BOOT_ID = None
SYSTEM_FIRST_BOOT = False
# Flag telling whether the system completed boot and is ready to use
SYSTEM_READY = False
# Flag telling whether the system is shutting down
SYSTEM_SHUTTING_DOWN = False


FIRST_INSTALL_SENTINEL = '/data/first-boot'

RE_KDUMP_CONFIGURED = re.compile(r'current state\s*:\s*(ready to kdump)', flags=re.M)


async def _update_birthday(middleware):
    while True:
        birthday = await middleware.call('system.get_synced_clock_time')
        if birthday:
            middleware.logger.debug('Updating birthday data')
            # update db with new birthday
            settings = await middleware.call('datastore.config', 'system.settings')
            await middleware.call(
                'datastore.update', 'system.settings', settings['id'], {'stg_birthday': birthday}, {'ha_sync': False}
            )
            break
        else:
            await asyncio.sleep(300)


async def _event_system(middleware, event_type, args):

    global SYSTEM_READY
    global SYSTEM_SHUTTING_DOWN
    if args['id'] == 'ready':
        SYSTEM_READY = True

        # Check if birthday is already set
        birthday = await middleware.call('system.birthday')
        if birthday is None:
            # try to set birthday in background
            asyncio.ensure_future(_update_birthday(middleware))

        if (await middleware.call('system.advanced.config'))['kdump_enabled']:
            cp = await run(['kdump-config', 'status'], check=False)
            if cp.returncode:
                middleware.logger.error('Failed to retrieve kdump-config status: %s', cp.stderr.decode())
            else:
                if not RE_KDUMP_CONFIGURED.findall(cp.stdout.decode()):
                    await middleware.call('alert.oneshot_create', 'KdumpNotReady', None)
                else:
                    await middleware.call('alert.oneshot_delete', 'KdumpNotReady', None)
        else:
            await middleware.call('alert.oneshot_delete', 'KdumpNotReady', None)

        if await middleware.call('system.first_boot'):
            asyncio.ensure_future(middleware.call('usage.firstboot'))

    if args['id'] == 'shutdown':
        SYSTEM_SHUTTING_DOWN = True


async def firstboot(middleware):
    global SYSTEM_FIRST_BOOT
    if os.path.exists(FIRST_INSTALL_SENTINEL):
        SYSTEM_FIRST_BOOT = True
        # Delete sentinel file before making clone as we
        # we do not want the clone to have the file in it.
        os.unlink(FIRST_INSTALL_SENTINEL)

        if await middleware.call('system.is_enterprise'):
            config = await middleware.call('datastore.config', 'system.advanced')
            await middleware.call('datastore.update', 'system.advanced', config['id'], {'adv_autotune': True})

        # Creating pristine boot environment from the "default"
        initial_install_be = 'Initial-Install'
        middleware.logger.info('Creating %r boot environment...', initial_install_be)
        activated_be = await middleware.call('bootenv.query', [['activated', '=', True]], {'get': True})
        try:
            await middleware.call('bootenv.create', {'name': initial_install_be, 'source': activated_be['realname']})
        except Exception:
            middleware.logger.error('Failed to create initial boot environment', exc_info=True)
        else:
            boot_pool = await middleware.call('boot.pool_name')
            cp = await run(
                'zfs', 'set', f'{"zectl" if osc.IS_LINUX else "beadm"}:keep=True',
                os.path.join(boot_pool, 'ROOT/Initial-Install')
            )
            if cp.returncode != 0:
                middleware.logger.error(
                    'Failed to set keep attribute for Initial-Install boot environment: %s', cp.stderr.decode()
                )


async def hook_license_update(middleware, prev_product_type, *args, **kwargs):
    if prev_product_type != 'ENTERPRISE' and await middleware.call('system.product_type') == 'ENTERPRISE':
        await middleware.call('system.advanced.update', {'autotune': True})


async def setup(middleware):
    global SYSTEM_BOOT_ID, SYSTEM_READY

    SYSTEM_BOOT_ID = str(uuid.uuid4())

    middleware.event_register('system', textwrap.dedent('''\
        Sent on system state changes.

        id=ready -- Finished boot process\n
        id=reboot -- Started reboot process\n
        id=shutdown -- Started shutdown process'''))

    if os.path.exists("/tmp/.bootready"):
        SYSTEM_READY = True
    else:
        await firstboot(middleware)

    settings = await middleware.call('system.general.config')
    await middleware.call('core.environ_update', {'TZ': settings['timezone']})

    middleware.logger.debug(f'Timezone set to {settings["timezone"]}')

    await middleware.call('system.general.set_language')
    await middleware.call('system.general.set_crash_reporting')

    middleware.event_subscribe('system', _event_system)
    middleware.register_event_source('system.health', SystemHealthEventSource)

    CRASH_DIR = '/data/crash'
    os.makedirs(CRASH_DIR, exist_ok=True)
    os.chmod(CRASH_DIR, 0o775)

    await middleware.call('sysctl.set_zvol_volmode', 2)

    middleware.register_hook('system.post_license_update', hook_license_update)
