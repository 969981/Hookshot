// (hookshot) vk_signed.h: a vendor's library loaded only when it is the vendor's own file, and the file that was checked.
#include "vk_signed.h"

#include "vk_internal.h"   // (logf)

#ifdef _WIN32
#include <softpub.h>
#include <wincrypt.h>
#include <wintrust.h>

#include <cwchar>
#include <vector>
#else
#include <grp.h>
#include <pwd.h>
#include <sys/stat.h>
#include <sys/vfs.h>
#include <unistd.h>

#include <cstdlib>
#endif

namespace mkwvk::signed_dll {

#ifdef _WIN32
namespace {
std::string narrow(const wchar_t* w) {   // (for the log: UTF-8, never the ANSI code page)
    std::string out;
    const int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
    if (n > 1) {
        out.resize((size_t)n - 1);
        WideCharToMultiByte(CP_UTF8, 0, w, -1, out.data(), n, nullptr, nullptr);
    }
    return out;
}

/// Whether Windows trusts the file's embedded signature and its signer's name is exactly `signer`.
///
/// Written out here rather than taken from Streamline's own sl_security.h on purpose: that header's verifyEmbeddedSignature
/// uses WINTRUST_SIGNATURE_SETTINGS, which the Windows SDK has and llvm-mingw's headers do not, and Hookshot builds for
/// Windows with either (third_party/a64-kit). This uses only what both have. Trust alone is not enough -- it would accept
/// anything signed by anyone Windows trusts -- so the subject name is read too.
bool signed_by(const std::wstring& path, const wchar_t* signer, const char* what) {
    WINTRUST_FILE_INFO file{};
    file.cbStruct = sizeof file;
    file.pcwszFilePath = path.c_str();
    GUID action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    WINTRUST_DATA data{};
    data.cbStruct = sizeof data;
    data.dwUIChoice = WTD_UI_NONE;
    data.fdwRevocationChecks = WTD_REVOKE_NONE;   // (a player may well be offline)
    data.dwUnionChoice = WTD_CHOICE_FILE;
    data.pFile = &file;
    data.dwStateAction = WTD_STATEACTION_VERIFY;
    const LONG status = WinVerifyTrust(nullptr, &action, &data);
    data.dwStateAction = WTD_STATEACTION_CLOSE;
    WinVerifyTrust(nullptr, &action, &data);
    if (status != ERROR_SUCCESS) {
        logf("vk: %s has no signature Windows trusts (status %#lx): not loading it", what, (unsigned long)status);
        return false;
    }
    HCERTSTORE store{};
    HCRYPTMSG msg{};
    if (!CryptQueryObject(CERT_QUERY_OBJECT_FILE, path.c_str(), CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
                          CERT_QUERY_FORMAT_FLAG_BINARY, 0, nullptr, nullptr, nullptr, &store, &msg, nullptr)) {
        logf("vk: %s's signature could not be read (error %lu): not loading it", what, GetLastError());
        return false;
    }
    bool ok = false;
    DWORD info_size = 0;
    // (each way this can fail says so: a refusal with no line read as "no such file" further on, with the file there)
    if (!CryptMsgGetParam(msg, CMSG_SIGNER_INFO_PARAM, 0, nullptr, &info_size) || !info_size) {
        logf("vk: %s's signer could not be read from its signature (error %lu): not loading it", what, GetLastError());
    } else {
        std::vector<unsigned char> buffer(info_size);
        if (!CryptMsgGetParam(msg, CMSG_SIGNER_INFO_PARAM, 0, buffer.data(), &info_size)) {
            logf("vk: %s's signer could not be read from its signature (error %lu): not loading it", what, GetLastError());
        } else {
            const auto* info = reinterpret_cast<CMSG_SIGNER_INFO*>(buffer.data());
            CERT_INFO want{};
            want.Issuer = info->Issuer;
            want.SerialNumber = info->SerialNumber;
            if (PCCERT_CONTEXT cert = CertFindCertificateInStore(store, X509_ASN_ENCODING, 0, CERT_FIND_SUBJECT_CERT, &want,
                                                                 nullptr)) {
                wchar_t name[256] = {};
                CertGetNameStringW(cert, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, nullptr, name, 256);
                // (exactly the vendor's name: a certificate whose name only contains it is anyone's)
                ok = wcscmp(name, signer) == 0;
                if (!ok)
                    logf("vk: %s is signed by \"%s\", not \"%s\": not loading it", what, narrow(name).c_str(),
                         narrow(signer).c_str());
                CertFreeCertificateContext(cert);
            } else {
                logf("vk: %s's signing certificate is not in its signature (error %lu): not loading it", what,
                     GetLastError());
            }
        }
    }
    CryptMsgClose(msg);
    CertCloseStore(store, 0);
    return ok;
}
}  // namespace

std::wstring wide(const std::string& utf8) {
    std::wstring out;
    const int n = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), (int)utf8.size(), nullptr, 0);
    if (n > 0) {
        out.resize((size_t)n);
        MultiByteToWideChar(CP_UTF8, 0, utf8.data(), (int)utf8.size(), out.data(), n);
    }
    return out;
}

HANDLE open_checked(const std::wstring& path, const wchar_t* signer, const char* what) {
    // (held from before the check until the caller has loaded it, sharing reads only: the file checked is the file loaded)
    const HANDLE held = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                                    FILE_ATTRIBUTE_NORMAL, nullptr);
    if (held == INVALID_HANDLE_VALUE) {
        logf("vk: could not open %s to check it (error %lu)", what, GetLastError());
        return INVALID_HANDLE_VALUE;
    }
    if (!signed_by(path, signer, what)) {
        CloseHandle(held);
        return INVALID_HANDLE_VALUE;
    }
    return held;
}

HMODULE load(const std::wstring& path, const wchar_t* signer, const char* what, DWORD flags) {
    const HANDLE held = open_checked(path, signer, what);
    if (held == INVALID_HANDLE_VALUE) {
        SetLastError(ERROR_INVALID_IMAGE_HASH);
        return nullptr;
    }
    HMODULE m = LoadLibraryExW(path.c_str(), nullptr, flags);
    const DWORD error = m ? 0 : GetLastError();
    CloseHandle(held);
    if (!m) SetLastError(error);
    return m;
}

bool preload_amd_providers(const std::string& dir, DWORD flags, HMODULE (*with)(const wchar_t* path)) {
    // (made whole: LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR takes only a full path, and the loader's own load of the same file
    // then finds this one by it)
    const std::wstring wdir = wide(dir);
    wchar_t full[MAX_PATH];
    const DWORD n = GetFullPathNameW(wdir.c_str(), MAX_PATH, full, nullptr);
    const std::wstring base = n && n < MAX_PATH ? std::wstring(full) : wdir;
    for (const char* name : {"amd_fidelityfx_upscaler_vk.dll", "amd_fidelityfx_framegeneration_vk.dll"}) {
        const std::wstring path = base + L"\\" + wide(name);
        if (GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES) continue;
        const HANDLE held = open_checked(path, kAmd, name);
        if (held == INVALID_HANDLE_VALUE) {
            logf("vk: AMD's FidelityFX loader in %s is not loaded: it would load %s unchecked", dir.c_str(), name);
            return false;
        }
        const HMODULE m = with ? with(path.c_str()) : LoadLibraryExW(path.c_str(), nullptr, flags);
        const DWORD error = m ? 0 : GetLastError();
        CloseHandle(held);
        if (!m) {
            logf("vk: %s in %s did not load (Windows error %lu): AMD's FidelityFX loader is not loaded either", name,
                 dir.c_str(), (unsigned long)error);
            return false;
        }
    }
    return true;
}
#else
namespace {
/// RX_VK_LIBRARY_CHECK: 1, the default, as safe() says; 0 loads what it would refuse (said in the log); 2 also refuses what
/// the default loads with a line: a file another account owns, and one on a drive that keeps no Unix permissions
int check_level() {
    static const int level = [] {
        const char* e = getenv("RX_VK_LIBRARY_CHECK");
        return e && *e ? atoi(e) : 1;
    }();
    return level;
}

/// The kind of filesystem `p` is on when it keeps no Unix owners or modes, else nullptr. NTFS (the ntfs3 driver, the old
/// ntfs, ntfs-3g through FUSE), exFAT and FAT show every file with the mount's uid and umask: Valve's guide mounts a Steam
/// NTFS drive with umask=000, so every file there reads 0777 whoever may really write it. (FUSE is every FUSE
/// filesystem: its f_type does not say which, and ntfs-3g is the one players have)
const char* permissionless(const std::string& p) {
    struct statfs fs{};
    if (statfs(p.c_str(), &fs) != 0) return nullptr;
    switch ((unsigned long)fs.f_type) {
    case 0x7366746eUL: return "NTFS, ntfs3";
    case 0x5346544eUL: return "NTFS";
    case 0x65735546UL: return "FUSE, ntfs-3g or another";
    case 0x2011bab0UL: return "exFAT";
    case 0x4d44UL: return "FAT";
    default: return nullptr;
    }
}

/// One file or folder, as a library loader may take it. Refused: writable by an account other than its owner, that is by
/// everyone, or by its group when that group is not this user's own (a user-private group: Ubuntu and its kin make files
/// 664 and folders 775 for every user, umask 002) and is a group on this PC. Loaded with a line: on a drive that keeps no
/// Unix permissions (the modes there are the mount's, not anyone's), or owned by another account than this user or root
/// (a drive made under another install, where the player had another uid). RX_VK_LIBRARY_CHECK: check_level
bool safe(const std::string& p, const char* what, const char* kind) {
    struct stat st{};
    if (stat(p.c_str(), &st) != 0) {
        logf("vk: %s: %s %s could not be read: not loading it", what, kind, p.c_str());
        return false;
    }
    const int level = check_level();
    if (const char* fs = permissionless(p)) {
        if (level >= 2) {
            logf("vk: %s: %s %s is on a drive that keeps no Unix permissions (%s): not loading it (RX_VK_LIBRARY_CHECK=2)",
                 what, kind, p.c_str(), fs);
            return false;
        }
        logf("vk: %s: %s %s is on a drive that keeps no Unix permissions (%s): loaded without checking who may write it",
             what, kind, p.c_str(), fs);
        return true;
    }
    // (a group no account here has, a drive's from another install, is no one's to write with)
    const bool own_group = st.st_gid == getegid() || !getgrgid(st.st_gid);
    if ((st.st_mode & S_IWOTH) || ((st.st_mode & S_IWGRP) && !own_group)) {
        if (level == 0) {
            logf("vk: %s: %s %s can be written by other accounts (mode %03o): loaded anyway (RX_VK_LIBRARY_CHECK=0)", what,
                 kind, p.c_str(), (unsigned)(st.st_mode & 0777));
            return true;
        }
        logf("vk: %s: %s %s can be written by other accounts (mode %03o): not loading it (chmod go-w it, or "
             "RX_VK_LIBRARY_CHECK=0: readme)",
             what, kind, p.c_str(), (unsigned)(st.st_mode & 0777));
        return false;
    }
    if (st.st_uid != geteuid() && st.st_uid != 0) {
        const bool here = getpwuid(st.st_uid) != nullptr;
        if (level >= 2) {
            logf("vk: %s: %s %s belongs to another account (uid %u): not loading it (RX_VK_LIBRARY_CHECK=2)", what, kind,
                 p.c_str(), (unsigned)st.st_uid);
            return false;
        }
        logf("vk: %s: %s %s belongs to %s (uid %u): loaded (RX_VK_LIBRARY_CHECK=2 refuses it)", what, kind, p.c_str(),
             here ? "another account on this PC" : "an account not on this PC (a drive from another install)",
             (unsigned)st.st_uid);
    }
    return true;
}
}  // namespace

bool folder_owned_safely(const std::string& dir, const char* what) { return safe(dir, what, "the folder"); }

bool owned_safely(const std::string& path, const char* what) {
    const size_t cut = path.find_last_of('/');
    const std::string dir = cut == std::string::npos ? std::string(".") : cut == 0 ? std::string("/") : path.substr(0, cut);
    return safe(path, what, "the file") && safe(dir, what, "its folder");
}
#endif

}  // namespace mkwvk::signed_dll
