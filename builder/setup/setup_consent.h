#ifndef HOOKSHOT_SETUP_CONSENT_H
#define HOOKSHOT_SETUP_CONSENT_H

#include "setup_plan.h"

typedef struct {
    int (*available)(const wchar_t *, uint64_t *, void *);
    int (*confirm)(const SetupInstallPlan *, uint64_t, int, void *);
    SetupPlanResult (*choose)(const wchar_t *, wchar_t *, void *);
    void *context;
} SetupConsentHooks;

/* The actual preflight loop shared by setup and its authored preview/tests.
   Its adapters may read metadata/disk state or show UI; none may install/write. */
static inline SetupPlanResult setup_consent(const wchar_t *initial, int fixed_source_root, const wchar_t *leaf,
                                            const char *manifest_sha, const SetupSpace *space,
                                            const SetupPlanPart *parts, unsigned count,
                                            const SetupConsentHooks *hooks, SetupInstallPlan *out) {
    wchar_t root[SETUP_PLAN_PATH];
    memset(out, 0, sizeof *out);
    if (!hooks || !hooks->available || !hooks->confirm || !hooks->choose || wcslen(initial) >= SETUP_PLAN_PATH)
        return SETUP_PLAN_BAD_PATH;
    memcpy(root, initial, (wcslen(initial) + 1) * sizeof(wchar_t));
    for (;;) {
        uint64_t available;
        SetupInstallPlan plan;
        if (!hooks->available(root, &available, hooks->context)) return SETUP_PLAN_BAD_PATH;
        SetupPlanResult result = setup_plan_create(root, leaf, manifest_sha, space, parts, count, available, &plan);
        if (result != SETUP_PLAN_OK && result != SETUP_PLAN_NO_SPACE &&
            !(result == SETUP_PLAN_PATH_TOO_LONG && plan.root[0])) return result;
        int choice = hooks->confirm(&plan, available, !fixed_source_root, hooks->context);
        if (choice == SETUP_CHOOSE_INSTALL) {
            if (!plan.ready) return result;
            *out = plan;
            return SETUP_PLAN_OK;
        }
        if (choice != SETUP_CHOOSE_CHANGE || fixed_source_root) return SETUP_PLAN_CANCELLED;
        result = hooks->choose(root, root, hooks->context);
        if (result != SETUP_PLAN_OK) return result;
    }
}

#endif
