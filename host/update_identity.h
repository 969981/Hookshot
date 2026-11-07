/* Release identity shared with the game-free update regression. */
#ifndef HOS_UPDATE_IDENTITY_H
#define HOS_UPDATE_IDENTITY_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

static inline int hos_update_hex(unsigned char c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 :
           c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

static inline int hos_update_revision_valid(const char *revision) {
    if (!revision || strlen(revision) != 40) return 0;
    for (size_t i = 0; i < 40; i++) if (hos_update_hex((unsigned char)revision[i]) < 0) return 0;
    return 1;
}

static inline int hos_update_identity_equal(const char *tag, const char *revision,
                                            const char *other_tag, const char *other_revision) {
    if (!tag || !*tag || !other_tag || strcmp(tag, other_tag) ||
        !hos_update_revision_valid(revision) || !hos_update_revision_valid(other_revision)) return 0;
    for (size_t i = 0; i < 40; i++)
        if (hos_update_hex((unsigned char)revision[i]) != hos_update_hex((unsigned char)other_revision[i])) return 0;
    return 1;
}

enum { HOS_UPDATE_UNKNOWN, HOS_UPDATE_CURRENT, HOS_UPDATE_SKIPPED, HOS_UPDATE_AVAILABLE };

static inline int hos_update_decision(const char *have_tag, const char *have_revision,
                                      const char *tag, const char *revision,
                                      const char *skip_tag, const char *skip_revision) {
    if (!have_tag || !*have_tag || !hos_update_revision_valid(have_revision) ||
        !tag || !*tag || !hos_update_revision_valid(revision)) return HOS_UPDATE_UNKNOWN;
    if (hos_update_identity_equal(tag, revision, have_tag, have_revision)) return HOS_UPDATE_CURRENT;
    return hos_update_identity_equal(tag, revision, skip_tag, skip_revision) ? HOS_UPDATE_SKIPPED : HOS_UPDATE_AVAILABLE;
}

static inline const char *hos_update_json_space(const char *p, const char *end) {
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) p++;
    return p;
}

/* Bound the value without mistaking a field in a string/nested object for a root field. */
static inline const char *hos_update_json_end(const char *p, const char *end, unsigned depth) {
    if (p == end || depth > 32) return NULL;
    if (*p == '"') {
        for (p++; p < end; p++) {
            if ((unsigned char)*p < 32) return NULL;
            if (*p == '"') return p + 1;
            if (*p == '\\' && ++p == end) return NULL;
        }
        return NULL;
    }
    if (*p == '{' || *p == '[') {
        const char close = *p == '{' ? '}' : ']';
        for (p++; p < end;) {
            if (*p == close) return p + 1;
            if (*p == '}' || *p == ']') return NULL;
            if (*p == '"' || *p == '{' || *p == '[') {
                p = hos_update_json_end(p, end, depth + 1);
                if (!p) return NULL;
            } else p++;
        }
        return NULL;
    }
    const char *start = p;
    while (p < end && *p != ',' && *p != '}' && *p != ']' && *p != ' ' && *p != '\r' && *p != '\n' && *p != '\t') p++;
    return p == start ? NULL : p;
}

static inline int hos_update_json_decode(const char *p, const char *end, char *out, size_t cap) {
    if (end - p < 2 || *p != '"' || end[-1] != '"' || !cap) return 0;
    size_t used = 0;
    for (p++; p < end - 1; p++) {
        uint32_t cp = (unsigned char)*p;
        if (cp < 32) return 0;
        if (cp == '\\') {
            if (++p >= end - 1) return 0;
            if (*p == 'u') {
                cp = 0;
                for (int i = 0; i < 4; i++) {
                    if (++p >= end - 1 || hos_update_hex((unsigned char)*p) < 0) return 0;
                    cp = cp * 16 + (uint32_t)hos_update_hex((unsigned char)*p);
                }
                if (cp >= 0xd800 && cp <= 0xdbff) {
                    if (end - p < 8 || p[1] != '\\' || p[2] != 'u') return 0;
                    p += 2;
                    uint32_t low = 0;
                    for (int i = 0; i < 4; i++) {
                        if (++p >= end - 1 || hos_update_hex((unsigned char)*p) < 0) return 0;
                        low = low * 16 + (uint32_t)hos_update_hex((unsigned char)*p);
                    }
                    if (low < 0xdc00 || low > 0xdfff) return 0;
                    cp = 0x10000 + ((cp - 0xd800) << 10) + low - 0xdc00;
                } else if (cp >= 0xdc00 && cp <= 0xdfff) return 0;
                if (!cp) return 0;
                unsigned char bytes[4];
                size_t count;
                if (cp < 0x80) bytes[0] = (unsigned char)cp, count = 1;
                else if (cp < 0x800) bytes[0] = (unsigned char)(0xc0 | cp >> 6), bytes[1] = (unsigned char)(0x80 | (cp & 63)), count = 2;
                else if (cp < 0x10000) bytes[0] = (unsigned char)(0xe0 | cp >> 12), bytes[1] = (unsigned char)(0x80 | (cp >> 6 & 63)), bytes[2] = (unsigned char)(0x80 | (cp & 63)), count = 3;
                else bytes[0] = (unsigned char)(0xf0 | cp >> 18), bytes[1] = (unsigned char)(0x80 | (cp >> 12 & 63)), bytes[2] = (unsigned char)(0x80 | (cp >> 6 & 63)), bytes[3] = (unsigned char)(0x80 | (cp & 63)), count = 4;
                if (used + count >= cap) return 0;
                memcpy(out + used, bytes, count);
                used += count;
                continue;
            }
            if (*p == '"' || *p == '\\' || *p == '/') cp = (unsigned char)*p;
            else if (*p == 'b') cp = '\b';
            else if (*p == 'f') cp = '\f';
            else if (*p == 'n') cp = '\n';
            else if (*p == 'r') cp = '\r';
            else if (*p == 't') cp = '\t';
            else return 0;
        }
        if (used + 1 >= cap) return 0;
        out[used++] = (char)cp;
    }
    out[used] = 0;
    return 1;
}

static inline int hos_update_json_field(const char *body, const char *end, const char *name,
                                        const char **value, const char **value_end) {
    const char *p = hos_update_json_space(body, end);
    if (p == end || *p++ != '{') return 0;
    while ((p = hos_update_json_space(p, end)) < end && *p != '}') {
        const char *key_end = hos_update_json_end(p, end, 0);
        char key[64];
        if (!key_end || !hos_update_json_decode(p, key_end, key, sizeof key)) return 0;
        p = hos_update_json_space(key_end, end);
        if (p == end || *p++ != ':') return 0;
        p = hos_update_json_space(p, end);
        const char *e = hos_update_json_end(p, end, 0);
        if (!e) return 0;
        if (!strcmp(key, name)) { *value = p; *value_end = e; return 1; }
        p = hos_update_json_space(e, end);
        if (p == end || (*p != ',' && *p != '}')) return 0;
        if (*p == ',') p++;
    }
    return 0;
}

static inline int hos_update_json_string(const char *body, const char *end, const char *name,
                                         char *out, size_t cap) {
    const char *value, *value_end;
    return hos_update_json_field(body, end, name, &value, &value_end) &&
           hos_update_json_decode(value, value_end, out, cap);
}

/* Git ref responses and annotated tag responses both identify the target in object. */
static inline int hos_update_ref_target(const char *body, char revision[41], char type[8]) {
    const char *value, *end;
    if (!hos_update_json_field(body, body + strlen(body), "object", &value, &end) || *value != '{' ||
        !hos_update_json_string(value, end, "sha", revision, 41) ||
        !hos_update_json_string(value, end, "type", type, 8) ||
        !hos_update_revision_valid(revision) || (strcmp(type, "commit") && strcmp(type, "tag"))) return 0;
    for (size_t i = 0; i < 40; i++) revision[i] = "0123456789abcdef"[hos_update_hex((unsigned char)revision[i])];
    return 1;
}

static inline int hos_update_encode_tag(const char *tag, char *out, size_t cap) {
    size_t used = 0;
    if (!tag || !*tag || !cap) return 0;
    for (; *tag; tag++) {
        const unsigned char c = (unsigned char)*tag;
        const int plain = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                          c == '-' || c == '_' || c == '.' || c == '~';
        if (c < 32 || c == 127 || c == '"' || c == '\\' || used + (plain ? 1 : 3) >= cap) return 0;
        if (plain) out[used++] = (char)c;
        else out[used++] = '%', out[used++] = "0123456789ABCDEF"[c >> 4], out[used++] = "0123456789ABCDEF"[c & 15];
    }
    out[used] = 0;
    return 1;
}
#endif
