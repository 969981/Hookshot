#ifndef HOOKSHOT_SETUP_REQUEST_H
#define HOOKSHOT_SETUP_REQUEST_H

#include "setup_plan.h"

static inline int setup_argument_char(wchar_t *text, size_t capacity, size_t *length, wchar_t value) {
    if (*length + 1 >= capacity) return 0;
    text[(*length)++] = value;
    text[*length] = 0;
    return 1;
}

/* Windows argv quoting, including a quote or trailing backslashes in a folder. */
static inline int setup_argument(wchar_t *text, size_t capacity, const wchar_t *value) {
    size_t length = wcslen(text);
    if (length && !setup_argument_char(text, capacity, &length, L' ')) return 0;
    if (!setup_argument_char(text, capacity, &length, L'"')) return 0;
    for (;;) {
        size_t slashes = 0;
        while (*value == L'\\') slashes++, value++;
        size_t count = (*value == L'"' || !*value) ? slashes * 2 : slashes;
        if (*value == L'"') count++;
        while (count--) if (!setup_argument_char(text, capacity, &length, L'\\')) return 0;
        if (!*value) break;
        if (!setup_argument_char(text, capacity, &length, *value++)) return 0;
    }
    return setup_argument_char(text, capacity, &length, L'"');
}

static inline int setup_request(int argc, wchar_t **argv, int *repair, wchar_t *forward, size_t capacity) {
    *repair = 0;
    if (!capacity) return 0;
    forward[0] = 0;
    for (int i = 1; i < argc; i++) {
        if (!wcscmp(argv[i], L"--repair")) {
            if (*repair) return 0;
            *repair = 1;
        } else if (!setup_argument(forward, capacity, argv[i])) return 0;
    }
    return 1;
}

#endif
