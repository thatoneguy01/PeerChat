class MembershipError(Exception):
    pass


class StaleTermError(MembershipError):
    pass
