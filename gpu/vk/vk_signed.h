// (hookshot) Loading another company's library from a folder the player can write to: NVIDIA's Streamline interposer
// (vk_streamline.cpp), AMD's FidelityFX loader (vk_fsr4.cpp, vk_ffx.cpp). Only the vendor's own file is loaded, and it is
// the file that was checked.
//
// Windows: the file is held open, sharing reads only (nothing can write, rename or delete it), from before its signature is
// checked until the caller has loaded it; Windows must trust the signature (WinVerifyTrust) and the signing certificate's
// name must be exactly the vendor's: "NVIDIA Corporation", "Advanced Micro Devices" (the names in their certificates' CN,
// read from NVIDIA's sl.interposer.dll and AMD's amd_fidelityfx_*_vk.dll on 2026-11-21; a name that only contains the
// vendor's is anyone's).
// Linux: shared objects are not signed, so the file and its folder must be writable by nobody but their owner (their group
// may write when that group is the player's own, Ubuntu's 775 folders, or no group on this PC): a library another account
// could put there is not loaded. What the filesystem cannot say is not refused, only logged: a drive that keeps no Unix
// permissions (NTFS, exFAT, FAT: Valve's guide mounts a Steam NTFS drive with umask=000), or an owner other than the player
// or root (a drive made under another install). RX_VK_LIBRARY_CHECK=0 loads what would be refused, =2 refuses those too.
#pragma once

#include <string>

#ifdef _WIN32
#include <windows.h>
#endif

namespace mkwvk::signed_dll {

#ifdef _WIN32
inline constexpr const wchar_t* kNvidia = L"NVIDIA Corporation";
inline constexpr const wchar_t* kAmd = L"Advanced Micro Devices";

/// A UTF-8 path (the executable's manifest makes narrow paths UTF-8: nx2/win_manifest.py) as Windows' wide one
std::wstring wide(const std::string& utf8);

/// `path` opened sharing reads only, once Windows trusts its signature and its signer is exactly `signer`; the caller loads
/// it while the handle is held and closes it after. INVALID_HANDLE_VALUE, with one line logged naming `what`, when not.
HANDLE open_checked(const std::wstring& path, const wchar_t* signer, const char* what);

/// open_checked, then LoadLibraryExW with `flags` (the caller's search: LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR and System32 or the
/// default folders), then the handle let go. nullptr when it is not the vendor's file or did not load (GetLastError then).
HMODULE load(const std::wstring& path, const wchar_t* signer, const char* what, DWORD flags);

/// AMD's FidelityFX loader loads its providers, amd_fidelityfx_upscaler_vk.dll and amd_fidelityfx_framegeneration_vk.dll,
/// from its own folder with no check of its own: one put in place of AMD's would run inside the game, the loader's check
/// notwithstanding. So each one in `dir` is loaded first, as load() loads (AMD's own file, held from its check to its load),
/// by its full path, and the loader then finds it loaded. `with` loads it in place of LoadLibraryExW(path, flags) (vk_ffx's
/// guarded load). False, with a line, when one is there and is not AMD's or did not load: the loader is then not loaded
/// either. One not there is the loader's to miss, as before. They stay loaded for the run.
bool preload_amd_providers(const std::string& dir, DWORD flags, HMODULE (*with)(const wchar_t* path) = nullptr);
#else
/// Whether `path` and its folder may be loaded from: no other account may write either (vk_signed.cpp safe; the log says why
/// not, and what was loaded without the check).
bool owned_safely(const std::string& path, const char* what);
/// The same for a folder alone (one a library loader searches: NVIDIA's NGX takes its runtimes from the folders it is given)
bool folder_owned_safely(const std::string& dir, const char* what);
#endif

}  // namespace mkwvk::signed_dll
