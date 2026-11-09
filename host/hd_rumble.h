/* HD rumble for Nintendo Switch Pro Controllers and Joy-Cons, through SDL's effect call (host/pad_sdl.c).

   The game's vibration values (nn::hid VibrationValue: {amp_low, freq_low, amp_high, freq_high}, amplitudes 0 to 1,
   frequencies in Hz, one per side of the controller) are what the Switch sends its controllers, so a Switch controller
   can play them as they are, both bands at their own frequency and on their own side, instead of two motor strengths.
   SDL's Switch driver (src/joystick/hidapi/SDL_hidapi_switch.c, SDL 3.2 on: HIDAPI_DriverSwitch_SendJoystickEffect)
   takes a whole rumble output report, 0x10, packet number, then 4 bytes per side, and writes it in place of its own
   rumble. SDL2's own driver and the other SDL drivers (the kernel's hid-nintendo, joysticks SDL combined, such as a
   Joy-Con pair) refuse it: those pads keep the motor path. Switch 2 controllers get their own packet (below).

   The encoding is the one documented by the community's reverse engineering (Switchbrew's HID "Rumble data" page;
   dekuNukem's Nintendo_Switch_Reverse_Engineering rumble_data_table.md, written from its formulas and tables):
   - a frequency's code is round(log2(Hz / 10) * 32); the high band takes code - 0x60 times 4 (0x004 to 0x1FC, 9 bits:
     81.75 to 1252.57 Hz), the low band code - 0x40 (0x01 to 0x7F: 40.88 to 626.29 Hz);
   - an amplitude's code, 0 to 100 (0 to 1.003), is round(log2(8.7 a) * 32) from 32 up, round(log2(17 a) * 16) from 16
     to 31, and below that the table's 2^(1/4) steps (a = 2^(code / 4) / 119.6, code 1 = 0.0078); the high band takes
     code * 2, the low band code / 2 + 0x40 with the code's odd bit as its 9th bit (0x8000). Codes over 100 are past what
     the table calls safe for the actuators: 100 is the top, as SDL's own table stops there;
   - the 4 bytes of a side: high frequency's low 8 bits; high amplitude | the high frequency's 9th bit; low frequency |
     the low amplitude's 9th bit (0x80); the low amplitude's low 8 bits. Silence is 00 01 40 40 (both bands at 0, at
     320 and 160 Hz), as SDL sends it.

   Switch 2 controllers (Pro Controller, Joy-Con 2) play the same values with SDL's Switch 2 driver
   (src/joystick/hidapi/SDL_hidapi_switch2.c) once it takes raw HD rumble: a packet of 0x02, then 5 bytes per side,
   left then right; the driver sends a Joy-Con its own side and refuses anything else, the Switch packet included.
   Until then it refuses them all, and these pads keep the motor path (its rumble at fixed frequencies). SDL reports
   them with the Switch controllers' types, so their USB ids tell them apart. A side's 5 bytes are the controller's
   encoding (SDL's EncodeHDRumble): four 10-bit fields, little endian: high band frequency, high band amplitude, low
   band frequency, low band amplitude.
   - a frequency's code is round(log2(Hz / 10) * 96), 0 to 1023: the Switch's scale (above), three times as fine, as
     measured on a Switch 2 Pro Controller with its own accelerometer (within about 1% from 20 to 357 Hz; there are no
     official numbers). SDL's fixed frequencies, 0x187 and 0x112, are 168 and 72 Hz on it;
   - an amplitude's code is linear, the game's 1.0 being HD_RUMBLE2_AMP_TOP: SDL's strongest rumble on these
     controllers (RUMBLE_MAX, 29000 of 65535, as a 10-bit field), past which the driver's effect clamps it too.

   Pure (no state, no SDL): tests/test_hd_rumble.py compiles it. */
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

#define HD_RUMBLE_PACKET_SIZE 10          /* (SDL's SwitchCommonOutputPacket_t: type, number, 2 x 4 bytes) */
#define HD_RUMBLE2_PACKET_SIZE 11         /* (Switch 2: type, 2 x 5 bytes) */
#define HD_RUMBLE_PACKET_MAX 11
#define HD_RUMBLE2_TYPE 0x02
#define HD_RUMBLE2_AMP_TOP 453            /* (29000 * 1023 / 65535, as SDL's 10-bit field has it: 29000 >> 6) */
#define HD_RUMBLE_REPORT 0x10             /* (k_eSwitchOutputReportIDs_Rumble) */
#define HD_RUMBLE_AMP_TOP 100             /* the amplitude code 1.003 has: the top the table calls safe */
#define HD_RUMBLE_GAP_NS 32000000LL       /* the least time between two packets (SDL: 30 ms; faster can turn a
                                             Bluetooth controller off, or the motors miss them over USB) */
#define HD_RUMBLE_REFRESH_NS 40000000LL   /* a rumble that goes on is sent again this often (SDL refreshes its own
                                             every 50 ms: the controller stops one it is not sent again) */

/* What kind of Switch controller SDL says a pad is: 0 none of them (the motor path), else the side(s) it has, and
   whether it is a Switch 2 one. */
enum {
    HD_RUMBLE_NONE, HD_RUMBLE_PRO, HD_RUMBLE_JOYCON_LEFT, HD_RUMBLE_JOYCON_RIGHT, HD_RUMBLE_JOYCON_PAIR,
    HD_RUMBLE2_PRO, HD_RUMBLE2_JOYCON_LEFT, HD_RUMBLE2_JOYCON_RIGHT, HD_RUMBLE2_JOYCON_PAIR
};
/* From SDL_GetGamepadType (SDL3: SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_PRO 7, then JOYCON_LEFT, RIGHT, PAIR) or
   SDL_GameControllerGetType (SDL2: SDL_CONTROLLER_TYPE_NINTENDO_SWITCH_PRO 5, JOYCON_LEFT 11, RIGHT 12, PAIR 13). */
static inline int hd_rumble_kind(int sdl_version, int type) {
    if (sdl_version == 3) return type >= 7 && type <= 10 ? HD_RUMBLE_PRO + (type - 7) : HD_RUMBLE_NONE;
    if (sdl_version == 2) return type == 5 ? HD_RUMBLE_PRO : type >= 11 && type <= 13 ? HD_RUMBLE_JOYCON_LEFT + (type - 11) : HD_RUMBLE_NONE;
    return HD_RUMBLE_NONE;
}
/* The kind, from the pad's USB ids too (SDL's src/joystick/usb_ids.h): Nintendo's Switch 2 controllers are Switch 2
   kinds whatever type SDL gives them, its GameCube controller none (a plain motor); other ids keep kind. */
static inline int hd_rumble_kind_ids(int kind, unsigned vendor, unsigned product) {
    if (vendor != 0x057e) return kind;
    switch (product) {
    case 0x2069: return HD_RUMBLE2_PRO;
    case 0x2067: return HD_RUMBLE2_JOYCON_LEFT;
    case 0x2066: return HD_RUMBLE2_JOYCON_RIGHT;
    case 0x2068: return HD_RUMBLE2_JOYCON_PAIR;
    case 0x2073: return HD_RUMBLE_NONE;
    default: return kind;
    }
}
static inline int hd_rumble_switch2(int kind) { return kind >= HD_RUMBLE2_PRO; }

/* A frequency's code, round(log2(Hz / 10) * 32), kept to [lo, hi]; a frequency that is not one (0, NaN) gets
   otherwise. */
static inline int hd_rumble_freq_code(float hz, int lo, int hi, int otherwise) {
    if (!(hz > 0.0f)) return otherwise;   /* (also NaN) */
    const double c = floor(log2((double)hz / 10.0) * 32.0 + 0.5);
    return c < lo ? lo : c > hi ? hi : (int)c;
}
/* An amplitude's code, 0 to HD_RUMBLE_AMP_TOP (see above). */
static inline int hd_rumble_amp_code(float amp) {
    if (!(amp > 0.0f)) return 0;   /* (also NaN) */
    double c;
    if (amp > 0.2275f) c = floor(log2(8.7 * amp) * 32.0 + 0.5);             /* (between codes 31 and 32: 0.225, 0.230) */
    else if (amp > 0.1150f) c = floor(log2(17.0 * amp) * 16.0 + 0.5);       /* (15 and 16: 0.112, 0.117) */
    else if (amp >= 0.0039f) c = floor(4.0 * log2(119.6 * amp) + 0.5);       /* (half of code 1's 0.0078: less is 0) */
    else return 0;
    if (c < 1) c = 1;   /* (code 1, 0.0078, is off the 2^(1/4) steps: the smallest that plays) */
    return c > HD_RUMBLE_AMP_TOP ? HD_RUMBLE_AMP_TOP : (int)c;
}

/* The silent side, 00 01 40 40. */
static inline void hd_rumble_neutral(uint8_t out[4]) {
    out[0] = 0x00, out[1] = 0x01, out[2] = 0x40, out[3] = 0x40;
}
/* One side's 4 bytes from the game's value {amp_low, freq_low, amp_high, freq_high}, its amplitudes scaled by percent
   (100 the game's own, 0 silence). Over 100, both bands get the same factor, the stronger stopping at 1 (as the motors'
   strength does: the bands keep their balance), and the top stays HD_RUMBLE_AMP_TOP. */
static inline float hd_rumble_scale(const float v[4], int percent) {
    float scale = percent > 0 ? (float)percent / 100.0f : 0.0f;
    const float top = v[0] > v[2] ? v[0] : v[2];
    if (scale > 1.0f && top * scale > 1.0f) scale = top > 1.0f ? 1.0f : 1.0f / top;   /* (NaN: not above, kept) */
    return scale;
}
static inline void hd_rumble_encode(const float v[4], int percent, uint8_t out[4]) {
    const float scale = hd_rumble_scale(v, percent);
    const int la = hd_rumble_amp_code(v[0] * scale), ha = hd_rumble_amp_code(v[2] * scale);
    if (!la && !ha) {
        hd_rumble_neutral(out);
        return;
    }
    const int lf = hd_rumble_freq_code(v[1], 0x41, 0xBF, 0x80) - 0x40;          /* (160 Hz when it has none) */
    const int hf = (hd_rumble_freq_code(v[3], 0x61, 0xDF, 0xA0) - 0x60) * 4;    /* (320 Hz) */
    const int ha2 = ha * 2, la9 = ((la & 1) << 15) | (la / 2 + 0x40);
    out[0] = (uint8_t)(hf & 0xFF);
    out[1] = (uint8_t)(ha2 | ((hf >> 8) & 0x01));
    out[2] = (uint8_t)(lf | ((la9 >> 8) & 0x80));
    out[3] = (uint8_t)(la9 & 0xFF);
}

/* The packet SDL's effect call takes, from the game's left and right sides' bytes: a Pro Controller (and a pair)
   plays each side on its own; a single Joy-Con plays its own side (SDL's packet has both: it gets it in both). */
static inline void hd_rumble_packet(int kind, const uint8_t left[4], const uint8_t right[4], uint8_t out[HD_RUMBLE_PACKET_SIZE]) {
    out[0] = HD_RUMBLE_REPORT;
    out[1] = 0;   /* (SDL numbers the packets itself) */
    const uint8_t *l = kind == HD_RUMBLE_JOYCON_RIGHT ? right : left, *r = kind == HD_RUMBLE_JOYCON_LEFT ? left : right;
    memcpy(out + 2, l, 4);
    memcpy(out + 6, r, 4);
}
static inline int hd_rumble_silent(const uint8_t packet[HD_RUMBLE_PACKET_SIZE]) {
    static const uint8_t n[4] = {0x00, 0x01, 0x40, 0x40};
    return !memcmp(packet + 2, n, 4) && !memcmp(packet + 6, n, 4);
}

/* Switch 2: a frequency's code (0 or NaN: otherwise), an amplitude's (0 to HD_RUMBLE2_AMP_TOP), a side's 5 bytes. */
static inline int hd_rumble2_freq_code(float hz, int otherwise) {
    if (!(hz > 0.0f)) return otherwise;   /* (also NaN) */
    const double c = floor(log2((double)hz / 10.0) * 96.0 + 0.5);
    return c < 0 ? 0 : c > 1023 ? 1023 : (int)c;
}
static inline int hd_rumble2_amp_code(float amp) {
    if (!(amp > 0.0f)) return 0;   /* (also NaN) */
    const double c = floor((double)amp * HD_RUMBLE2_AMP_TOP + 0.5);
    return c > HD_RUMBLE2_AMP_TOP ? HD_RUMBLE2_AMP_TOP : (int)c;
}
static inline void hd_rumble2_pack(int high_freq, int high_amp, int low_freq, int low_amp, uint8_t out[5]) {
    const uint64_t bits = (uint64_t)(high_freq & 0x3FF) | (uint64_t)(high_amp & 0x3FF) << 10 |
                          (uint64_t)(low_freq & 0x3FF) << 20 | (uint64_t)(low_amp & 0x3FF) << 30;
    for (int i = 0; i < 5; i++) out[i] = (uint8_t)(bits >> 8 * i);
}
/* One side's 5 bytes from the game's value, as hd_rumble_encode (the same strength scaling); silence is both bands at
   0 at 320 and 160 Hz (e0 01 00 18 00). */
static inline void hd_rumble2_encode(const float v[4], int percent, uint8_t out[5]) {
    const float scale = hd_rumble_scale(v, percent);
    const int la = hd_rumble2_amp_code(v[0] * scale), ha = hd_rumble2_amp_code(v[2] * scale);
    if (!la && !ha) {
        hd_rumble2_pack(480, 0, 384, 0, out);   /* (320 and 160 Hz) */
        return;
    }
    hd_rumble2_pack(hd_rumble2_freq_code(v[3], 480), ha, hd_rumble2_freq_code(v[1], 384), la, out);
}
/* The packet SDL's Switch 2 driver takes: the game's two sides as they are (the driver sends a Joy-Con its own). */
static inline void hd_rumble2_packet(const uint8_t left[5], const uint8_t right[5], uint8_t out[HD_RUMBLE2_PACKET_SIZE]) {
    out[0] = HD_RUMBLE2_TYPE;
    memcpy(out + 1, left, 5);
    memcpy(out + 6, right, 5);
}

/* Whether a pad's packet goes out now: packet the one the game asks for, sent the last one sent (silence before the
   first), sent_ns when, ever whether there was one. Never sooner than HD_RUMBLE_GAP_NS after the last; a change goes
   at once, then the latest when the gap is over (what the game asks for in between is not sent: a newer value takes its
   place); a rumble that goes on is sent again every HD_RUMBLE_REFRESH_NS; silence that was sent is not sent again. */
static inline int hd_rumble_due(const uint8_t packet[HD_RUMBLE_PACKET_SIZE], const uint8_t sent[HD_RUMBLE_PACKET_SIZE],
                                int ever, long long now, long long sent_ns) {
    if (ever && now - sent_ns < HD_RUMBLE_GAP_NS) return 0;
    if (memcmp(packet + 2, sent + 2, 8)) return 1;
    return ever && !hd_rumble_silent(sent) && now - sent_ns >= HD_RUMBLE_REFRESH_NS;
}
/* Switch 2: a change goes at once; nothing else. SDL's driver keeps a packet and sends it to the controller in its own
   rumble reports, at its own pace, until the next one: no gap or refresh here. */
static inline int hd_rumble2_due(const uint8_t packet[HD_RUMBLE2_PACKET_SIZE], const uint8_t sent[HD_RUMBLE2_PACKET_SIZE]) {
    return memcmp(packet, sent, HD_RUMBLE2_PACKET_SIZE) != 0;
}

/* A pad's HD rumble state, as the poll moves it: unknown until its first packet; on while SDL takes them; off (the
   motor path, for as long as the pad is open) once SDL refuses one, or when it has no effect call, is no Switch
   controller, or HOS_HD_RUMBLE=0. */
enum { HD_RUMBLE_UNKNOWN, HD_RUMBLE_ON, HD_RUMBLE_OFF };
static inline int hd_rumble_start(int kind, int have_call, int env_off) {
    return kind != HD_RUMBLE_NONE && have_call && !env_off ? HD_RUMBLE_UNKNOWN : HD_RUMBLE_OFF;
}
static inline int hd_rumble_after_send(int ok) { return ok ? HD_RUMBLE_ON : HD_RUMBLE_OFF; }
