// (hookshot, #1370) The temporal upscalers' depth with a hidden character: the depth draws of a skinned mesh the game has
// stopped showing are left out. Plain code with no Vulkan calls: tests/test_hidden_mesh_depth.py compiles it alone, and
// vk_draw.cpp (hidden_depth_draw) gives it the draws and has the why. From magicbeanenjoyerguyperson's patch on #1370.
//
// A mesh is a vertex buffer (stream 0's address), followed from frame to frame while draws come from it. In each frame it
// counts its depth draws (skinned draws that write depth and no colour into the scene's depth target) and its colour draws
// (draws from it that write colour, into any target). It is hidden while, in each of the kHiddenAfter frames before this
// one, it had depth draws and no colour, and before those it had colour, and it was followed in every frame since.
// A hidden mesh's depth draws are left out until a frame with colour from it: from the frame after that one, they are drawn
// again. The game draws them before its colour, so the frame its colour comes back still has them left out, and the game's
// colour pass, which tests depth EQUAL against them, then shows the mesh a frame late (Link, as the camera leaves him). A
// mesh that blinks with kHiddenAfter frames off or more loses its first frame back each time: Frame::back counts those.
// Never left out: a draw the game asks about with an occlusion query (it decides by the answer what it shows), a draw into
// another depth target, a mesh that never had colour, and a mesh that was not followed in the frame before (a frame
// skipped, the table full).
// One Tracker for all the drawing threads: the replay draws one submission at a time (vk_replay.cpp replay_mu), but the
// game submits from several threads, and a mesh's depth and colour may come from two of them.
#pragma once

#include <cstdint>

namespace mkwvk::hidden_mesh {

constexpr uint32_t kSlots = 256;   // (a power of two; a frame's skinned meshes are a few dozen, Link 30 of them)
// (frames in a row without colour before a mesh's depth draws are left out. The game's colour pass tests its depth EQUAL
// against these draws (tests/test_equal_depth_margin.py), so a mesh whose depth is left out in a frame with colour is not
// shown in that frame: with 1, a mesh that blinks (colour every other frame) would never be shown again, and a colour lost
// for one frame would be lost for two. 3: those are drawn as before, and the camera in Link is still fixed)
constexpr uint32_t kHiddenAfter = 3;

struct Mesh {
    uint64_t key = 0;       // (the vertex buffer's address; 0: an empty place)
    uint64_t program = 0;   // (its first depth draw's program, for the log)
    uint32_t depth = 0;     // (this frame: its depth draws)
    uint32_t left = 0;      // (this frame: of those, the ones left out)
    uint32_t colour = 0;    // (this frame: its colour draws)
    uint32_t bare = 0;      // (the frames in a row, up to the one before this, with depth draws and no colour)
    bool seen = false;      // (it had colour before those, and was followed in every frame since)
};

/// Hidden in this frame: its depth draws are to be left out
inline bool hidden(const Mesh& m) { return m.seen && m.bare >= kHiddenAfter; }
/// Hidden in the next frame, once this one is over
inline bool hidden_next(const Mesh& m) { return m.seen && m.depth && !m.colour && m.bare + 1 >= kHiddenAfter; }

struct Frame {
    uint64_t frame = ~0ull;
    uint64_t scene = 0;          // (the scene's depth target in this frame, as the caller took it; 0: not yet)
    Mesh mesh[kSlots];
    uint32_t meshes = 0;         // (places taken)
    uint32_t over = 0;           // (draws of meshes the table had no room for: those are not followed)
    uint32_t depth_draws = 0;    // (the depth draws counted)
    uint32_t left_out = 0;       // (of those, the ones left out)
    uint32_t stencil = 0;        // (of those left out, the ones with a stencil test on: what they might have written)
    uint32_t kept_query = 0;     // (depth draws of hidden meshes drawn all the same: the game asks about them)
    uint32_t scene_colour = 0, other_colour = 0;   // (the colour draws counted: on the scene's depth target, and not)
    uint64_t out_key = 0, out_program = 0;         // (the first mesh a draw was left out of)
    uint32_t hidden = 0;         // (set when the frame is over: its meshes hidden in the next one)
    uint32_t back = 0;           // (set when the frame is over: its meshes with colour whose depth draws were left out)
};

/// A frame that is over, for the log: begin() gives it back
struct Summary {
    uint64_t frame = ~0ull;      // (~0: no frame was over)
    uint32_t meshes = 0;         // (meshes with depth draws)
    uint32_t coloured = 0;       // (of those, the ones with colour too)
    uint32_t bare = 0;           // (of those, the ones without colour)
    uint32_t hidden = 0;         // (meshes hidden in the next frame)
    uint32_t colour_only = 0;    // (meshes with colour and no depth draw)
    uint32_t inherited = 0;      // (meshes the frame before left hidden for this one)
    const Mesh* one = nullptr;   // (a hidden one, else one without colour, for the log)
    const Frame* f = nullptr;    // (the frame's own counts)
};

/// What the caller knows of a draw
struct Draw {
    uint64_t key = 0;        // (vertex stream 0's address; 0: none, or the draw reads no vertex from it)
    uint64_t program = 0;
    uint64_t target = 0;     // (its depth target, any number that names it; 0: none)
    bool skinned = false;    // (a skinned draw into a target the scene can be: vk_draw.cpp skin_check)
    bool colour = false;     // (it writes colour, into any target)
    bool depth_write = false;
    bool stencil = false;    // (its stencil test is on)
    bool query = false;      // (the game asks about it with an occlusion query)
};

class Tracker {
public:
    Frame now, before;   // (before: the frame right before now's, or empty)

    /// A draw of `frame` comes: when it is a later frame than now's, now's frame is over (its summary given back, now kept as
    /// before when the two are in a row) and a new frame begins
    Summary begin(uint64_t frame) {
        Summary s;
        if (now.frame == frame) return s;
        if (now.frame != ~0ull) close(s);
        if (now.frame + 1 == frame) before = now;
        else before = Frame{};   // (no draw between: nothing is known of the frame before)
        now = Frame{};
        now.frame = frame;
        return s;
    }
    /// Some mesh is followed in this frame or the one before: only then is a draw that is no skinned one looked up
    bool active() const { return now.meshes || before.meshes; }
    /// The buffer is a followed mesh's, in this frame or the one before
    bool followed(uint64_t key) const { return find(now, key) || find(before, key); }

    /// A draw of now's frame, counted. `rule`: leaving out is on. `is_scene(target)`: whether a depth target is the scene's
    /// in this frame (asked at the frame's first skinned depth draw). True: the draw is to be left out
    template <class IsScene>
    bool draw(const Draw& d, bool rule, IsScene&& is_scene) {
        if (!d.key || (!d.skinned && !followed(d.key))) return false;
        if (d.colour) {   // (colour from a followed mesh or a skinned draw, into any target)
            colour(d.key, d.target && d.target == now.scene);
            return false;
        }
        if (!d.skinned || !d.depth_write || !d.target) return false;
        // (a skinned draw that writes depth alone: only into the depth target of the scene, its camera's. A frame can have
        // other targets of the scene's size with skinned draws)
        if (!now.scene) {
            if (!is_scene(d.target)) return false;
            now.scene = d.target;
        } else if (d.target != now.scene) {
            return false;
        }
        return depth(d.key, d.program, d.query, d.stencil, rule);
    }

    /// A draw from the mesh `key` that writes colour (into any target; `scene`: with the scene's depth target)
    void colour(uint64_t key, bool scene) {
        Mesh* m = place(key);
        if (!m) return;
        m->colour++;
        (scene ? now.scene_colour : now.other_colour)++;
    }
    /// A skinned draw from the mesh `key` that writes depth and no colour into the scene's depth target. True: to be left out
    bool depth(uint64_t key, uint64_t program, bool query, bool stencil, bool rule) {
        Mesh* m = place(key);
        if (!m) return false;
        if (!m->depth++) m->program = program;
        now.depth_draws++;
        if (!hidden(*m)) return false;
        if (query) return now.kept_query++, false;
        if (!rule) return false;
        if (!now.left_out++) now.out_key = key, now.out_program = program;
        m->left++;
        now.stencil += stencil;
        return true;
    }

    static const Mesh* find(const Frame& f, uint64_t key) {
        if (!key) return nullptr;
        uint32_t at = slot(key);
        for (uint32_t n = 0; n < kSlots; n++, at = (at + 1) & (kSlots - 1)) {
            const Mesh& m = f.mesh[at];
            if (m.key == key) return &m;
            if (!m.key) return nullptr;
        }
        return nullptr;
    }

private:
    static uint32_t slot(uint64_t key) { return (uint32_t)((key * 0x9E3779B97F4A7C15ull) >> 56) & (kSlots - 1); }

    /// The mesh's place in now's table, made on its first draw of the frame from its place in the frame before; nullptr
    /// (counted in `over`) when the table is full
    Mesh* place(uint64_t key) {
        if (!key) return nullptr;
        uint32_t at = slot(key);
        for (uint32_t n = 0; n < kSlots; n++, at = (at + 1) & (kSlots - 1)) {
            Mesh& m = now.mesh[at];
            if (m.key == key) return &m;
            if (m.key) continue;
            if (now.meshes >= kSlots * 3 / 4) break;   // (kept a quarter empty: a search ends)
            const Mesh* was = find(before, key);
            now.meshes++;
            m.key = key;
            m.seen = was && (was->seen || was->colour);
            m.bare = was && was->depth && !was->colour ? was->bare + 1 : 0;
            return &m;
        }
        now.over++;
        return nullptr;
    }

    /// now's frame is over: its counts into `s`, and its meshes hidden in the next frame into now.hidden
    void close(Summary& s) {
        s.frame = now.frame;
        const Mesh* bare_one = nullptr;
        for (const Mesh& m : now.mesh) {
            if (!m.key) continue;
            now.back += m.colour && m.left;
            if (!m.depth) {
                s.colour_only += m.colour != 0;
                continue;
            }
            s.meshes++;
            if (m.colour) {
                s.coloured++;
                continue;
            }
            s.bare++;
            if (!bare_one) bare_one = &m;
            if (hidden_next(m) && !s.hidden++) s.one = &m;
        }
        if (!s.one) s.one = bare_one;
        now.hidden = s.hidden;
        s.inherited = before.frame != ~0ull && before.frame + 1 == now.frame ? before.hidden : 0;
        closed = now;   // (the summary points into this copy, which stays until the next frame is over)
        s.f = &closed;
        if (s.one) s.one = &closed.mesh[s.one - now.mesh];
    }
    Frame closed;   // (the frame that was over last)
};

} // namespace mkwvk::hidden_mesh
