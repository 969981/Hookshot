#include "../host/update_identity.h"
#include <stdio.h>

#define CHECK(name, value) do { if (!(value)) { fprintf(stderr, "FAIL %s at line%d\n", name, __LINE__); return 1; } } while (0)

#ifdef HOS_TEST_ORIGINAL_TAG_ONLY
/* The prior native guard: a matching tag suppresses every corrected source build. */
static int original_decision(const char *have, const char *have_revision, const char *tag, const char *revision,
                             const char *skipped, const char *skipped_revision) {
    (void)have_revision; (void)revision; (void)skipped_revision;
    return !strcmp(tag, have) || !strcmp(tag, skipped) ? HOS_UPDATE_CURRENT : HOS_UPDATE_AVAILABLE;
}
#define hos_update_decision original_decision
#endif

int main(void) {
    const char *old = "e900faa8bf49547bd9d5a98aa5be5a9e7dea042e";
    const char *next = "170ce53ca3d69309b3f5151a334136be8b4c38d6";
    const char *tag = "foliage-linux-updater";
    CHECK("same tag corrected source is offered", hos_update_decision(tag, old, tag, next, "", "") == HOS_UPDATE_AVAILABLE);
    CHECK("same exact identity is current", hos_update_decision(tag, next, tag, next, "", "") == HOS_UPDATE_CURRENT);
    CHECK("new tag is offered", hos_update_decision("older", next, tag, next, "", "") == HOS_UPDATE_AVAILABLE);
    CHECK("legacy is unknown, never current or an offer loop", hos_update_decision(tag, "", tag, next, "", "") == HOS_UPDATE_UNKNOWN);
    CHECK("missing remote is unknown", hos_update_decision(tag, old, tag, "", "", "") == HOS_UPDATE_UNKNOWN);
    CHECK("skip exact build", hos_update_decision(tag, old, tag, next, tag, next) == HOS_UPDATE_SKIPPED);
    CHECK("old skip cannot hide correction", hos_update_decision(tag, old, tag, next, tag, old) == HOS_UPDATE_AVAILABLE);
    CHECK("tag-only skip cannot hide correction", hos_update_decision(tag, old, tag, next, tag, "") == HOS_UPDATE_AVAILABLE);
    CHECK("not game build id", !hos_update_revision_valid("e0f3ccfa"));
    CHECK("uppercase source SHA accepted", hos_update_identity_equal(tag, next, tag, "170CE53CA3D69309B3F5151A334136BE8B4C38D6"));
    char value[128], sha[41], type[8], encoded[384];
    const char *latest = "{\"body\":\"a \\\"tag_name\\\" example\",\"nested\":{\"tag_name\":\"wrong\"},\"tag_name\":\"foliage-linux-updater\"}";
    CHECK("top-level tag only", hos_update_json_string(latest, latest + strlen(latest), "tag_name", value, sizeof value) && !strcmp(value, tag));
    const char *unicode = "{\"tag_name\":\"fix/\\u00e9-\\ud83d\\ude80\"}";
    CHECK("JSON unicode", hos_update_json_string(unicode, unicode + strlen(unicode), "tag_name", value, sizeof value) &&
          !strcmp(value, "fix/\xc3\xa9-\xf0\x9f\x9a\x80"));
    CHECK("URL component escaped", hos_update_encode_tag("fix/foliage", encoded, sizeof encoded) && !strcmp(encoded, "fix%2Ffoliage"));
    CHECK("URL small capacity rejected", !hos_update_encode_tag("a/b", encoded, 3));
    CHECK("control and argument quotes rejected", !hos_update_encode_tag("x\ny", encoded, sizeof encoded) &&
          !hos_update_encode_tag("x\"y", encoded, sizeof encoded));
    const char *commit = "{\"object\":{\"sha\":\"170ce53ca3d69309b3f5151a334136be8b4c38d6\",\"type\":\"commit\",\"url\":\"unused\"}}";
    CHECK("commit ref", hos_update_ref_target(commit, sha, type) && !strcmp(sha, next) && !strcmp(type, "commit"));
    const char *annotated = "{\"sha\":\"e900faa8bf49547bd9d5a98aa5be5a9e7dea042e\",\"object\":{\"type\":\"commit\",\"sha\":\"170ce53ca3d69309b3f5151a334136be8b4c38d6\"}}";
    CHECK("annotated object target, not outer SHA", hos_update_ref_target(annotated, sha, type) && !strcmp(sha, next));
    const char *tag_ref = "{\"object\":{\"type\":\"tag\",\"sha\":\"e900faa8bf49547bd9d5a98aa5be5a9e7dea042e\"}}";
    CHECK("annotated ref followed", hos_update_ref_target(tag_ref, sha, type) && !strcmp(type, "tag"));
    CHECK("no arbitrary root SHA", !hos_update_ref_target("{\"sha\":\"170ce53ca3d69309b3f5151a334136be8b4c38d6\"}", sha, type));
    CHECK("unknown ref rejected", !hos_update_ref_target("{\"object\":{\"type\":\"tree\",\"sha\":\"170ce53ca3d69309b3f5151a334136be8b4c38d6\"}}", sha, type));
    const char *broken = "{\"tag_name\":\"unfinished";
    CHECK("truncated response rejected", !hos_update_json_string(broken, broken + strlen(broken), "tag_name", value, sizeof value));
    puts("update identity: source/skip/legacy/ref checks passed");
    return 0;
}
