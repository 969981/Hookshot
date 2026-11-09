"""A GPU without mesh shaders (RDNA1, issue 640) runs task and mesh programs as compute shaders.

The console writes a mesh program's vertices and primitives to an internal stage buffer, and Vulkan's
VK_EXT_mesh_shader hands them to the rasterizer from there. A replay only needs the records: here the
task and mesh programs are ordinary compute shaders, their epilogues write the vertices to a record
buffer, and a generated vertex stage (drawing an ordinary graphics pipeline) reads one corner a
vertex from it. This checks that path end to end in the sources: the device enables it with buffer
device addresses, the translator builds the compute stages and the generated vertex stage, the
dispatches run before the pass, and the draw is indirect: the mesh workgroups take records for the corners they
output (an atomic allocator in the records' header) and add them to its vertex count. Primitives land in the order
the workgroups took them, so blended mesh geometry can draw out of order (as in Lichii's fallback).

python -I -B tests/test_mesh_fallback.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"
SHADER = VK / "vk_shader.cpp"
DRAW = VK / "vk_draw.cpp"
MAIN = VK / "vk_main.cpp"
HEADER = VK / "vk_shader.h"
EMIT = VK / "eden" / "shader_recompiler" / "backend" / "spirv" / "emit_spirv.cpp"
MESH = VK / "eden" / "shader_recompiler" / "backend" / "spirv" / "emit_spirv_mesh.cpp"
SPIRV = VK / "eden" / "shader_recompiler" / "backend" / "spirv" / "emit_spirv.h"
VMA = VK / "vk_vma.cpp"


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    main_cpp = MAIN.read_text(encoding="utf-8")
    if "dev.mesh_fallback = !dev.mesh_shader && f12.bufferDeviceAddress" not in main_cpp:
        raise SystemExit("vk_main.cpp: no fallback without buffer device addresses")
    if "issue 640 fallback" not in main_cpp:
        raise SystemExit("vk_main.cpp: the fallback is not logged")
    if "if (!dev.mesh_shader && !dev.mesh_fallback" not in main_cpp:
        raise SystemExit("vk_main.cpp: the no-mesh-shader warning ignores the fallback")

    program = block(HEADER.read_text(encoding="utf-8"), "struct Program {")
    for field in ("bool fallback", "bool fallback_task", "fallback_topology", "fallback_vertices_per_workgroup",
                  "fallback_record_words", "fallback_payload_words", "fallback_task_threads"):
        if field not in program:
            raise SystemExit("Program has no %s" % field)

    mesh_shader = SHADER.read_text(encoding="utf-8")
    translate = block(mesh_shader, "void translate_mesh_into(")
    if "rt.mesh_fallback = fallback;" not in translate or "mrt.mesh_fallback = fallback;" not in translate:
        raise SystemExit("the task and mesh stages are not emitted as fallback stages")
    if "vrt.mesh_fallback_vertex = true;" not in translate or "vrt.mesh_fallback = true;" not in translate:
        raise SystemExit("no generated vertex stage")
    if "mesh_t->program.stage = Shader::Stage::VertexB;" not in translate:
        raise SystemExit("the generated vertex stage is not emitted as a vertex stage")
    if "mesh_t->program.info = Shader::Info{};" not in translate or \
            "mesh_t->program.info.stores = saved_info.stores;" not in translate:
        raise SystemExit("the generated vertex stage is not emitted with only the mesh program's stores")
    if "v.info = mesh_t->program.info;" in translate:
        raise SystemExit("the vertex stage still declares the mesh program's descriptors")
    if "vrt.per_primitive_generics.set(" not in translate:
        raise SystemExit("the generated vertex stage does not know the mesh program's per-primitive generics")
    if "fs->program.info.interpolation[" not in translate or "Shader::Interpolation::Flat" not in translate:
        raise SystemExit("the fallback fragment stage still declares PerPrimitiveEXT")
    if "VK_SHADER_STAGE_COMPUTE_BIT : VK_SHADER_STAGE_TASK_BIT_EXT" not in translate or \
            "VK_SHADER_STAGE_COMPUTE_BIT : VK_SHADER_STAGE_MESH_BIT_EXT" not in translate:
        raise SystemExit("the task and mesh stages are not compute stages in the fallback")
    if "p.fallback_task_threads = task_t ? task_t->program.workgroup_size[0] : 1;" not in translate:
        raise SystemExit("the task's threads are not kept for the mesh slot range")

    emit = EMIT.read_text(encoding="utf-8")
    entry = block(emit, "void DefineEntryPoint(")
    if "spv::ExecutionModel::GLCompute" not in entry or "ctx.mesh_fallback" not in entry:
        raise SystemExit("the fallback stages have no GLCompute entry point")
    if "spv::ExecutionModel::MeshEXT" not in entry or "spv::ExecutionModel::TaskEXT" not in entry:
        raise SystemExit("native mesh shaders were removed")

    mesh = MESH.read_text(encoding="utf-8")
    if "void MeshFallbackEpilogue(" not in mesh or "void EmitMeshFallbackVertexBody(" not in mesh:
        raise SystemExit("the fallback epilogue or vertex body is missing")
    task = block(mesh, "void TaskEpilogue(")
    if "ctx.OpULessThan(ctx.U1, LocalInvocation(ctx), count)" not in task:
        raise SystemExit("a task starts a mesh workgroup for every thread, not only the meshlets it kept")
    epilogue = block(mesh, "void MeshFallbackEpilogue(")
    if "atomic_add(MESH_FALLBACK_ALLOC_WORD, taken)" not in epilogue or \
            "atomic_add(MESH_FALLBACK_DRAW_WORD, taken)" not in epilogue:
        raise SystemExit("a mesh workgroup does not take its records and add them to the draw's count")
    if "MESH_FALLBACK_CAPACITY" not in epilogue:
        raise SystemExit("a mesh workgroup past the slots is not left out")
    body = block(mesh, "void EmitMeshFallbackVertexBody(")
    if "ctx.Const(MESH_FALLBACK_HEADER_WORDS)" not in body or "ctx.vertex_index" not in body:
        raise SystemExit("the vertex stage does not read the record of its VertexIndex past the header")
    # (an output component the mesh program does not write is zero, as in the console's stage buffer: undefined, AMD drew
    # Kokiri Forest's flowers glowing, with specks of colour in the bushes)
    if "ctx.OpStore(info.id, ctx.f32_zero_value);" not in body:
        raise SystemExit("the generated vertex stage leaves the outputs the mesh program does not write undefined")
    if "per_primitive_generics" not in body or "spv::Decoration::Flat" not in body:
        raise SystemExit("the generated vertex stage does not declare its per-primitive outputs Flat")

    spirv = SPIRV.read_text(encoding="utf-8")
    if "MESH_FALLBACK_CAPACITY" not in spirv or "MESH_FALLBACK_PAYLOAD_PUSH_OFFSET" not in spirv or \
            "MESH_FALLBACK_RECORDS_PUSH_OFFSET" not in spirv:
        raise SystemExit("the fallback's push constants and capacity are not declared")
    if "MESH_FALLBACK_COUNTER_PUSH_OFFSET" in spirv:
        raise SystemExit("the removed atomic counter is still pushed")

    draw = DRAW.read_text(encoding="utf-8")
    if "bool fallback_record(VkCommandBuffer cmd, Program& p, const DrawArgs& d)" not in draw:
        raise SystemExit("vk_draw.cpp has no fallback dispatch")
    if "vkCmdDispatchIndirect(cmd, mesh_conv_buf, mesh_conv_off);" not in draw:
        raise SystemExit("an indirect mesh draw does not dispatch from its converted arguments")
    if "if (p.fallback && d.mesh && !fallback_record(cmd, p, d)) SKIP_HERE();" not in draw:
        raise SystemExit("the fallback dispatches do not run before the pass")
    if "vkCmdDrawIndirect(cmd, fallback_draw_records, 0, 1, sizeof(VkDrawIndirectCommand));" not in draw:
        raise SystemExit("the fallback is not drawn from the records' header as an indirect draw")
    if "VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT" not in block(draw, "bool make_fallback_buffers("):
        raise SystemExit("the records buffer cannot be read as indirect arguments")
    if "vkCmdFillBuffer(cmd, f->records, 4, 4, 1);" not in draw:
        raise SystemExit("the draw's instance count is not set to one")
    if "fallback_payload_address" not in draw or "MESH_FALLBACK_PAYLOAD_PUSH_OFFSET" not in draw:
        raise SystemExit("the fallback's buffer addresses are not pushed")
    if "VK_WHOLE_SIZE" not in draw:
        raise SystemExit("a payload a task did not write is not cleared before the mesh dispatch")
    if "VMA_ALLOCATOR_CREATE_BUFFER_DEVICE_ADDRESS_BIT" not in VMA.read_text(encoding="utf-8"):
        raise SystemExit("the allocator does not make the fallback's buffers addressable")
    if "dynamic_state(cmd, s, at, topo, d.mesh && !p.fallback);" not in draw:
        raise SystemExit("a fallback draw does not use the mesh program's topology")
    if "if (p.fallback) topo = p.fallback_topology;" not in draw:
        raise SystemExit("the fallback's points, lines or triangles are not used")
    if "p->mesh != dev.mesh_shader || p->fallback != dev.mesh_fallback" not in mesh_shader:
        raise SystemExit("a cached program from a GPU with the other mesh path is used")
    # (spirv-val on a real cache, scripts/validate_spirv.py: the push block's member is an index, not the byte offset the host
    # writes it at (index 64 into ResolutionInfo), and a PhysicalStorageBuffer access says its alignment (VUID 04708))
    import re
    if re.search(r"PushU64\(ctx,\s*MESH_FALLBACK_\w*PUSH_OFFSET", mesh):
        raise SystemExit("a pushed address is read by its byte offset, not its member index")
    if re.search(r"ctx\.Op(Load\(\s*ctx\.U32\[1\],|Store\()\s*(BufferWord|BufferWordAt|PayloadWord)\(", mesh):
        raise SystemExit("a fallback buffer word is loaded or stored without Aligned (LoadWord/StoreWord)")
    # (a task thread's slot gets the whole payload, not the words of its own lane; a mesh workgroup's index in its task is the
    # slot modulo the task's threads, by the payload's address 0 and by its workgroup id)
    if "ctx.OpUDiv(ctx.U32[1], FallbackSlot(ctx), ctx.Const(ctx.runtime_info.mesh_fallback_task_threads))" not in mesh:
        raise SystemExit("a mesh workgroup does not read its task workgroup's payload")
    if "Strided(ctx, ctx.mesh_info.payload_words, ctx.mesh_threads, LocalInvocation(ctx)" not in block(mesh, "void TaskEpilogue(EmitContext& ctx)"):
        raise SystemExit("a task workgroup's threads do not write its payload between them")
    if "ctx.Const(ctx.runtime_info.mesh_fallback_task_threads)" not in mesh:
        raise SystemExit("a mesh workgroup reads its slot, not its index in its task, at the payload's address 0")
    get_set = (EMIT.parent / "emit_spirv_context_get_set.cpp").read_text(encoding="utf-8")
    if "mesh_fallback_task_threads" not in block(get_set, "Id EmitWorkgroupId(EmitContext& ctx)"):
        raise SystemExit("a fallback mesh workgroup's id is its slot, not its index in its task")
    if "mrt.mesh_fallback_task_threads = task_t" not in mesh_shader:
        raise SystemExit("the mesh stage is not told its task's threads")
    # (one buffer pair a recording thread shares between its programs: a pair per program was 2.2 GB for 95 programs on a
    # 4 GB card. Shared, each draw's reset waits for the last draw that read them)
    buffers = block(draw, "bool fallback_buffers_of(const Program& p, FallbackBuffers*& out)")
    if "fallback_shared" not in buffers:
        raise SystemExit("each fallback program has buffers of its own (gigabytes on a 4 GB card)")
    record = block(draw, "bool fallback_record(VkCommandBuffer cmd, Program& p, const DrawArgs& d)")
    reset = record[record.index("end_pass(cmd);"):record.index("vkCmdFillBuffer(cmd, f->records")]
    if "full_barrier(cmd);" not in reset:
        raise SystemExit("a fallback draw resets the shared buffers before the last draw has read them")
    # (a task's mesh workgroups are dispatched from the count its started threads take in the records' header, each writing
    # its slot to the payload buffer's map: with DrawMeshTasksIndirect (the floor grass) the CPU knows no task count, and
    # groups * threads was 0, so no mesh workgroup ran. A mesh workgroup reads its slot from the map in the entry block)
    if "MESH_FALLBACK_DISPATCH_WORD" not in block(mesh, "void TaskEpilogue(EmitContext& ctx)"):
        raise SystemExit("a task's started threads do not count the mesh dispatch")
    if "vkCmdDispatchIndirect(cmd, f->records, (VkDeviceSize)Shader::Backend::SPIRV::MESH_FALLBACK_DISPATCH_WORD * 4)" not in record:
        raise SystemExit("a task program's mesh workgroups are not dispatched from the count the tasks took")
    if "ctx.mesh_fallback_slot =" not in block(mesh, "void EmitMeshPrologue(EmitContext& ctx)"):
        raise SystemExit("a mesh workgroup does not read its slot from its task's map")
    # (a GPU with mesh shaders draws as before #640: its native mesh pipelines keep their task and mesh stages (#640 left them
    # out of every pipeline, the RTX 4060 Ti's device loss), its layouts push what they did, its vertex input is stage 0's,
    # and its task and mesh SPIR-V loads and stores as main's)
    graphics = block(draw, "VkPipeline build_graphics(const Program& p, const NvnState& s, const Targets& at, VkPrimitiveTopology topo, int part) {")
    if "if (p.fallback && st.vk_stage == VK_SHADER_STAGE_COMPUTE_BIT) continue;" not in graphics or \
            "VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_TASK_BIT_EXT | VK_SHADER_STAGE_MESH_BIT_EXT))" in graphics:
        raise SystemExit("a native mesh pipeline is made without its task and mesh stages")
    if "dev.mesh_fallback ? kPushMax : Shader::Backend::SPIRV::MESH_FIRST_PUSH_OFFSET + 8" not in draw or "kPushSize" in draw:
        raise SystemExit("pipeline layouts push the fallback's addresses on a GPU without the fallback")
    if "if (!p.fallback) return &p.stages[0].info;" not in block(draw, "const Shader::Info* vertex_info(const Program& p)"):
        raise SystemExit("a program not drawn by the fallback takes its vertex input from another stage than before")
    if "if (!FallbackStage(ctx)) {" not in block(mesh, "Id LoadWord(EmitContext& ctx, Id pointer)") or \
            "if (!FallbackStage(ctx)) {" not in block(mesh, "void StoreWord(EmitContext& ctx, Id pointer, Id value)"):
        raise SystemExit("native task and mesh stages load and store differently from main")
    if "const bool fallback{dev.mesh_fallback};" not in mesh_shader or "dev.mesh_fallback = !dev.mesh_shader" not in main_cpp:
        raise SystemExit("the fallback is not only for a GPU without mesh shaders")
    # (the Insert menu's Mesh shader fallback: Auto (0, the default) only on a GPU without mesh shaders, Off (1) never, On (2) on
    # every GPU, its mesh shaders then not turned on. A restart row, not the preset's)
    menu = (VK / "vk_menu.cpp").read_text(encoding="utf-8")
    menu_h = (VK / "vk_menu.h").read_text(encoding="utf-8")
    if "int mesh_fallback = 0;" not in menu_h:
        raise SystemExit("the Mesh shader fallback option does not default to Auto")
    if '{TAB_GRAPHICS, "Mesh shader fallback", &opts.mesh_fallback, 0, 2, 1, tx_mesh_fallback,' not in menu:
        raise SystemExit("the Insert menu has no Mesh shader fallback row")
    if "r.value != &opts.mesh_fallback" not in menu:
        raise SystemExit("changing Mesh shader fallback switches the Quality preset to Custom")
    if "opts.mesh_fallback != 1" not in main_cpp.split("dev.mesh_fallback = ")[1].split(";")[0]:
        raise SystemExit("Off in the menu does not turn the fallback off")
    forced = main_cpp[main_cpp.index("if (opts.mesh_fallback == 2) {"):main_cpp.index("optional(VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME")]
    if "dev.mesh_shader = false;" not in forced or "optional(VK_EXT_MESH_SHADER_EXTENSION_NAME, dev.mesh_shader);" not in forced:
        raise SystemExit("On in the menu does not leave a GPU's mesh shaders off for the fallback")
    # (the live switch, hookshot_fallback_debug.txt: a fallback draw it names is left out before anything is recorded)
    if "if (fallback_debug_skip(p)) return false;" not in record:
        raise SystemExit("the fallback's live debug switch is not asked before a fallback draw")
    pipelines = block(draw, "bool fallback_compute_pipelines(Program& p)")
    if "compare_exchange_strong(none, 1)" not in pipelines or "jobs_push" not in pipelines:
        raise SystemExit("the fallback's compute pipelines are compiled in the draw, not on a worker (AMD: seconds each)")
    if "fallback_build(*job)" not in pipelines.split("auto build")[1] or \
            "guarded_compute" not in block(draw, "void fallback_build(Program& q)"):
        raise SystemExit("the fallback's compute pipelines are not made in the worker's job")
    # (and before play, with the other compute programs: AMD took up to 12 s on one, and the door at the start was white)
    precompile = block(draw, "void precompile_run(void (*progress)(size_t done, size_t total))")
    if "fallbacks.push_back(&p)" not in precompile or "fallback_build(p)" not in precompile:
        raise SystemExit("the precompile leaves the mesh fallback's compute pipelines to play")
    if "kind & (1 | 4)" not in block(mesh_shader, "size_t program_cache_load_some("):
        raise SystemExit("the precompile loads only the mesh fallback programs play has drawn (white until compiled)")
    print("mesh shader fallback (compute records and a vertex stage) ok")


if __name__ == "__main__":
    main()
