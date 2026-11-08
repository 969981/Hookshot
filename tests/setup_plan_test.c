#include <stdio.h>
#include <stdlib.h>
#include "setup_plan.h"
#include "fixture_data.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)
static int effect(const SetupInstallPlan *plan, void *context) {
    (void)plan;
    (*(int *)context)++;
    return 1;
}
static int join(wchar_t *out, const wchar_t *base, const wchar_t *tail) {
    int n = swprintf(out, SETUP_PLAN_PATH, L"%ls\\%ls", base, tail);
    return n > 0 && n < SETUP_PLAN_PATH;
}
int wmain(int argc, wchar_t **argv) {
    CHECK(argc == 4);
    wchar_t parent[SETUP_PLAN_PATH], root[SETUP_PLAN_PATH], expected[SETUP_PLAN_PATH], other[SETUP_PLAN_PATH];
    SetupRootKind kind;
    SetupSpace space;
    SetupInstallPlan plan;
    SetupPlanPart parts[] = {{PART_A_SHA, PART_A_SIZE}, {PART_B_SHA, PART_B_SIZE}};
    CHECK(setup_space_parse(GOOD_SPACE, sizeof GOOD_SPACE - 1, MANIFEST_SHA, &space) == SETUP_PLAN_OK);
    CHECK(join(parent, argv[1], L"new-parent"));
    CHECK(setup_root_child(parent, root, &kind) == SETUP_PLAN_OK && kind == SETUP_ROOT_NEW);
    if (!wcscmp(argv[2], L"metadata")) {
        CHECK(space.count == 2 && space.parts[0].expanded == 17 && space.parts[1].expanded == 0);
        CHECK(space.parts[0].files == 1 && space.parts[1].files == 1);
        CHECK(setup_plan_create(root, L"v1", MANIFEST_SHA, &space, parts, 2, UINT64_MAX, &plan) == SETUP_PLAN_OK);
        CHECK(plan.ready && plan.expanded == 17 && plan.compressed == PART_A_SIZE + PART_B_SIZE);
        CHECK(plan.files == 2 && plan.entries == 2 && plan.max_relative_units == 25);
        CHECK(plan.logical_reserve == PART_A_SIZE + PART_B_SIZE + 17);
    } else if (!wcscmp(argv[2], L"malformed")) {
        SetupSpace invalid = space;
        CHECK(setup_space_parse("HookshotSetupSpace 2\n", 21, MANIFEST_SHA, &invalid) == SETUP_PLAN_BAD_METADATA);
        CHECK(invalid.count == 0);
        CHECK(setup_space_parse(GOOD_SPACE, sizeof GOOD_SPACE - 1, WRONG_MANIFEST_SHA, &invalid) == SETUP_PLAN_BAD_METADATA);
        CHECK(setup_space_parse(DUPLICATE_SPACE, sizeof DUPLICATE_SPACE - 1, MANIFEST_SHA, &invalid) == SETUP_PLAN_BAD_METADATA);
        CHECK(setup_space_parse(OVERFLOW_SPACE, sizeof OVERFLOW_SPACE - 1, MANIFEST_SHA, &invalid) == SETUP_PLAN_BAD_METADATA);
        CHECK(setup_space_parse(UNKNOWN_SPACE, sizeof UNKNOWN_SPACE - 1, MANIFEST_SHA, &invalid) == SETUP_PLAN_BAD_METADATA);
    } else if (!wcscmp(argv[2], L"totals")) {
        CHECK(setup_plan_create(root, L"v1", MANIFEST_SHA, &space, parts, 1, UINT64_MAX, &plan) == SETUP_PLAN_MISSING_SPACE);
        CHECK(!plan.ready);
        parts[1].sha = UNKNOWN_PART_SHA;
        CHECK(setup_plan_create(root, L"v1", MANIFEST_SHA, &space, parts, 2, UINT64_MAX, &plan) == SETUP_PLAN_MISSING_SPACE);
        parts[1].sha = PART_B_SHA;
        parts[0].compressed = UINT64_MAX;
        CHECK(setup_plan_create(root, L"v1", MANIFEST_SHA, &space, parts, 2, UINT64_MAX, &plan) == SETUP_PLAN_OVERFLOW);
        parts[0].compressed = PART_A_SIZE;
        CHECK(setup_plan_create(root, L"v1", MANIFEST_SHA, &space, parts, 2, PART_A_SIZE + PART_B_SIZE + 16, &plan) == SETUP_PLAN_NO_SPACE);
        CHECK(!plan.ready);
    } else if (!wcscmp(argv[2], L"roots")) {
        CHECK(join(parent, argv[1], L"empty-parent"));
        CHECK(setup_root_child(parent, root, &kind) == SETUP_PLAN_OK && kind == SETUP_ROOT_EMPTY);
        CHECK(join(parent, argv[1], L"foreign-parent"));
        CHECK(setup_root_child(parent, root, &kind) == SETUP_PLAN_UNKNOWN_ROOT);
        CHECK(join(parent, argv[1], L"owned-parent"));
        CHECK(setup_root_child(parent, root, &kind) == SETUP_PLAN_OK && kind == SETUP_ROOT_OWNED);
        CHECK(setup_owned_version(root, L"v1"));
        CHECK(!setup_owned_version(root, L"unrelated"));
        CHECK(!setup_owned_version(root, L"..\\unrelated"));
        CHECK(!setup_owned_version(root, L"CON.txt"));
    } else if (!wcscmp(argv[2], L"unicode")) {
        CHECK(join(parent, argv[1], L"space \u96ea"));
        CHECK(setup_root_child(parent, root, &kind) == SETUP_PLAN_OK && kind == SETUP_ROOT_EMPTY);
    } else if (!wcscmp(argv[2], L"source")) {
        CHECK(join(parent, argv[1], L"owned-parent\\HookshotBuilder\\versions\\v1\\HookshotBuilder\\HookshotBuilder-Setup.exe"));
        CHECK(join(expected, argv[1], L"owned-parent\\HookshotBuilder"));
        CHECK(join(other, argv[1], L"other-parent\\HookshotBuilder"));
        CHECK(setup_preferred_root(parent, other, 1, root) == 1 && setup_path_equal(root, expected));
        CHECK(join(parent, argv[1], L"portable.exe"));
        CHECK(setup_preferred_root(parent, other, 1, root) == 0 && !root[0]);
        CHECK(setup_preferred_root(parent, other, 0, root) == 2 && setup_path_equal(root, other));
        CHECK(setup_preferred_root(parent, L"C:\\missing\\HookshotBuilder", 0, root) == 0 && !root[0]);
    } else if (!wcscmp(argv[2], L"gate")) {
        int writes = 0;
        CHECK(setup_plan_create(root, L"v1", MANIFEST_SHA, &space, parts, 2, UINT64_MAX, &plan) == SETUP_PLAN_OK);
        CHECK(setup_plan_begin(&plan, SETUP_CHOOSE_CANCEL, effect, &writes) == SETUP_PLAN_CANCELLED && writes == 0);
        CHECK(setup_plan_begin(&plan, SETUP_CHOOSE_CHANGE, effect, &writes) == SETUP_PLAN_CANCELLED && writes == 0);
        CHECK(!setup_plan_remember(&plan, 0, effect, &writes) && writes == 0);
        CHECK(setup_plan_begin(&plan, SETUP_CHOOSE_INSTALL, effect, &writes) == SETUP_PLAN_OK && writes == 1);
        CHECK(setup_plan_remember(&plan, 1, effect, &writes) && writes == 2);
        plan.root_kind = SETUP_ROOT_OWNED; /* A previously observed ownership fact no longer matches the filesystem. */
        CHECK(setup_plan_begin(&plan, SETUP_CHOOSE_INSTALL, effect, &writes) == SETUP_PLAN_STALE && writes == 2);
        plan.ready = 0;
        CHECK(setup_plan_begin(&plan, SETUP_CHOOSE_INSTALL, effect, &writes) == SETUP_PLAN_MISSING_SPACE && writes == 2);
    } else if (!wcscmp(argv[2], L"paths")) {
        CHECK(!setup_leaf(L"..") && !setup_leaf(L"C:escape") && !setup_leaf(L"v1.") && !setup_leaf(L"NUL"));
        CHECK(setup_leaf(L"honest-eta-a598b9f"));
        wchar_t long_path[400] = L"C:\\";
        for (unsigned i = 3; i < 350; i++) long_path[i] = L'x';
        long_path[350] = 0;
        CHECK(setup_root_validate(long_path, root, &kind) == SETUP_PLAN_PATH_TOO_LONG);
        CHECK(setup_root_validate(L"relative\\HookshotBuilder", root, &kind) == SETUP_PLAN_BAD_PATH);
        CHECK(setup_root_validate(L"\\\\?\\C:\\HookshotBuilder", root, &kind) == SETUP_PLAN_BAD_PATH);
        CHECK(join(parent, argv[1], L"new-parent"));
        CHECK(setup_root_child(parent, root, &kind) == SETUP_PLAN_OK);
        space.parts[0].max_relative_units = 32767;
        CHECK(setup_plan_create(root, L"v1", MANIFEST_SHA, &space, parts, 2, UINT64_MAX, &plan) == SETUP_PLAN_PATH_TOO_LONG);
        CHECK(!plan.ready);
        CHECK(wcslen(argv[3]) == 141);
        space.parts[0].max_relative_units = 21;
        CHECK(setup_plan_create(argv[3], L"v1", MANIFEST_SHA, &space, parts, 2, UINT64_MAX, &plan) == SETUP_PLAN_PATH_TOO_LONG);
        CHECK(!plan.ready);
    } else {
        CHECK(0);
    }
    puts("PASS: actual setup plan contract, read-only roots and explicit effect gates");
    return 0;
}
