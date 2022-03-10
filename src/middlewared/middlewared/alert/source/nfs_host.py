from middlewared.alert.base import AlertClass, SimpleOneShotAlertClass, AlertCategory, AlertLevel


class NFSHostnameLookupFailAlertClass(AlertClass, SimpleOneShotAlertClass):
    category = AlertCategory.SHARING
    level = AlertLevel.WARNING
    title = "NFS shares reference hosts that could not be resolved"
    text = "NFS shares refer to the following unresolvable hosts: %(hosts)s"
