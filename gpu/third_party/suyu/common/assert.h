// Rexcomp replacement for suyu's common/assert.h: same macros; a failed assertion prints and carries on, as suyu's
// soft asserts do in release builds.
#pragma once
#include <cstdio>
#include <stdexcept>
#include <string>

namespace Common {
inline void AssertFail(const char* file, int line, const char* what) {
    std::fprintf(stderr, "[shader] assertion failed at %s:%d: %s\n", file, line, what);
}
[[noreturn]] inline void Unreachable(const char* file, int line) {
    throw std::logic_error(std::string("unreachable code reached at ") + file + ":" + std::to_string(line));
}
} // namespace Common

// each assertion reports its first 3 failures
#define ASSERT_MSG(_a_, ...)                                                                       \
    do {                                                                                           \
        if (!(_a_)) [[unlikely]] {                                                                 \
            static int assert_reports_;                                                            \
            if (assert_reports_++ < 3) ::Common::AssertFail(__FILE__, __LINE__, #_a_);             \
        }                                                                                          \
    } while (0)
#define ASSERT(_a_) ASSERT_MSG(_a_, "")
// (hookshot) "Can't happen" is never undefined behaviour: a malformed program (a broken or malicious mod, a game bug) reached
// switch defaults the encodings were thought to rule out, and __builtin_unreachable let the compiler run on into whatever came
// next. Now it throws: the shader translation's callers catch std::exception (gpu/vk/vk_shader.cpp: the program is left out
// and logged); anywhere else nothing catches it and the process ends through std::terminate, a defined stop
#define UNREACHABLE_MSG(...)                                                                       \
    do {                                                                                           \
        ::Common::AssertFail(__FILE__, __LINE__, "unreachable");                                   \
        ::Common::Unreachable(__FILE__, __LINE__);                                                 \
    } while (0)
#define UNREACHABLE() UNREACHABLE_MSG("")
#define DEBUG_ASSERT(_a_) ((void)0)
#define DEBUG_ASSERT_MSG(_a_, ...) ((void)0)
#define UNIMPLEMENTED() ASSERT(false && "Unimplemented!")
#define UNIMPLEMENTED_MSG(...) ASSERT_MSG(false, __VA_ARGS__)
#define UNIMPLEMENTED_IF(cond) ASSERT((!(cond)) && "Unimplemented!")
#define UNIMPLEMENTED_IF_MSG(cond, ...) ASSERT_MSG(!(cond), __VA_ARGS__)
#define ASSERT_OR_EXECUTE(_a_, _b_)                                                                \
    do {                                                                                           \
        ASSERT(_a_);                                                                               \
        if (!(_a_)) [[unlikely]] {                                                                 \
            _b_                                                                                    \
        }                                                                                          \
    } while (0)
#define ASSERT_OR_EXECUTE_MSG(_a_, _b_, ...) ASSERT_OR_EXECUTE(_a_, _b_)
