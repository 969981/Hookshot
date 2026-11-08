"""Verify native arm64 acquire/release forms become fenced translated operations."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "third_party", "a64-kit", "lift"))
import a64emit

instructions = [
    (0x1000, 0xC85FFC20),  # LDAXR X0, [X1]
    (0x1004, 0xC802FC20),  # STLXR W2, X0, [X1]
    (0x1008, 0xC8608440),  # LDAXP X0, X1, [X2]
    (0x100C, 0xC82394C4),  # STLXP W3, X4, X5, [X6]
]
texts, flags = a64emit.translate(instructions)
if any(flags):
    raise SystemExit("arm64 acquire/release instruction was not translated")
for index, ordering in ((0, "ACQUIRE"), (1, "RELEASE"), (2, "ACQUIRE"), (3, "RELEASE")):
    if "__atomic_thread_fence(__ATOMIC_%s)" % ordering not in texts[index]:
        raise SystemExit("missing %s fence in translated instruction %#x" % (ordering, instructions[index][1]))
print("arm64 exclusive acquire/release instructions compile to fenced translated operations.")
