"""Which API operations belong to which use case. The s8 job payload names use cases; this table turns that into the set of
operations the deployed backend exposes (the "generic backend switched on by the payload" model)."""

# Operations every deployment exposes regardless of use case.
COMMON_OPS = {"createAccessToken", "verifyIdentity", "searchKnowledge"}

# use case -> operationIds.
USE_CASE_OPS = {
    "PCS": {"getClaimStatus"},
    "PAS": {"getAuthorizationStatus", "submitAuthorizationDocument"},
    "PEV": {"verifyEligibility"},
    "PAP": {"createAppeal", "getAppealStatus"},
    "PPI": {"getClaimPayments", "createPaymentCorrection"},
    "PON": {"getProviderOnboarding", "updateLocationAvailability"},
    # Member-facing use cases.
    "MBN": {"getMemberBenefits"},
    "MCI": {"listMemberClaims", "getMemberClaim"},
    "MPS": {"searchProviders"},
    "MPC": {"getMemberPcp", "changePcp"},
    "MAS": {"getAppointmentSlots", "bookAppointment", "listMemberAppointments", "cancelAppointment"},
}

def ops_for(use_cases):
    """Union of common operations and those of the requested use cases. Unknown use case codes raise KeyError so a bad payload fails loudly."""
    ops = set(COMMON_OPS)
    for u in use_cases: ops |= USE_CASE_OPS[u]
    return ops

ALL_OPS = set().union(COMMON_OPS, *USE_CASE_OPS.values())
