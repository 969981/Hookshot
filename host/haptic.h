/* DualSense haptics (host/haptic.c): the game's own HD Rumble values, amp_low / freq_low / amp_high / freq_high per
   hand, played by the DualSense's voice coils through its own USB audio endpoint, by RenHDrum's DSP
   (third_party/renhdrum, MIT OR Apache-2.0). The four values are kept as they are: nothing here flattens them into two
   motor strengths on the way (host/nn_hid.c's rumble_map still does that, for every pad that is not driven here).

   What drives a pad, from the menu's Haptic feedback row (opts.haptic) and the pad itself:
     Auto               a DualSense on a cable: DualSense haptics; a DualSense over Bluetooth: the standard rumble (the
                        multichannel endpoint needs a cable); a Switch Pro or Joy-Con: its own HD Rumble packets, which
                        host/hd_rumble.h and host/pad_sdl.c send as they always did; anything else: the standard rumble.
     Standard rumble    SDL's rumble, for every pad.
     DualSense haptics  the same as Auto, but a pad that is not a DualSense falls back to the standard rumble and says so.
     Off                nothing at all: no haptics, no standard rumble.

   The menu's Rumble strength is the one strength: 0 (Off) stops every backend here, and the percent scales the values
   once, in hos_haptic_push. A DualSense the haptics drive is sent no SDL rumble at the same time (hos_haptic_owns).

   Windows only: RenHDrum ships as renhdrum.dll beside the game (nx2/build.py). Elsewhere, and when the DLL or the audio
   endpoint is missing, every pad keeps the standard rumble and hos_haptic_status says why. */
#pragma once
#include <stdint.h>

/* the menu's Haptic feedback row (gpu/vk/vk_menu.h's opts.haptic, option HOS_OPT_HAPTIC_MODE) */
enum { HOS_HAPTIC_AUTO = 0, HOS_HAPTIC_STANDARD = 1, HOS_HAPTIC_DUALSENSE = 2, HOS_HAPTIC_OFF = 3 };

/* what drives the pad now: the Haptic status row's line (option HOS_OPT_HAPTIC_STATUS) */
enum {
    HOS_HAPTIC_DS_USB = 0,            /* DualSense haptics (USB) */
    HOS_HAPTIC_DS_BT = 1,             /* DualSense over Bluetooth: the standard rumble */
    HOS_HAPTIC_SWITCH_HD = 2,         /* the Switch pad's own HD Rumble */
    HOS_HAPTIC_STANDARD_RUMBLE = 3,   /* SDL's rumble */
    HOS_HAPTIC_DISABLED = 4,          /* Haptic feedback Off, or Rumble strength Off */
    HOS_HAPTIC_WAITING = 5,           /* a DualSense on a cable, its audio endpoint not there yet */
    HOS_HAPTIC_UNAVAILABLE = 6,       /* no renhdrum.dll, or one this build cannot use */
    HOS_HAPTIC_NO_PAD = 7,            /* no controller open */
    HOS_HAPTIC_STATUS_N
};

void hos_haptic_mode(int mode);        /* HOS_HAPTIC_*: the menu's row, read every poll (host/pad_sdl.c) */
void hos_haptic_strength(int percent); /* the menu's Rumble strength, 0 (off) to 150: scales the values once */
void hos_haptic_pad_added(void *pad, int vendor, const char *name, int wireless, int switch_hd);
void hos_haptic_pad_removed(void *pad);
void hos_haptic_push(int hand, const float value[4]); /* the game's amp_low, freq_low, amp_high, freq_high */
int hos_haptic_owns(void *pad);        /* 1: this pad's rumble is the haptics': send it no SDL rumble */
int hos_haptic_test(void);             /* 1 when a test started (it stops by itself, well under a second) */
int hos_haptic_testing(void);          /* 1 while the test plays (the motors buzz for it too) */
int hos_haptic_status(void);           /* HOS_HAPTIC_*: what drives the pad */
void hos_haptic_shutdown(void);
