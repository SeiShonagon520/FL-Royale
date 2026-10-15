"""The JNI bridge for PvE waves must agree with the Java declaration exactly.

A native method is bound by name and descriptor only. If the C++ export and the
Java `native` declaration disagree on the method name, the parameter order, or
the parameter types, the C++ still compiles, the Java still compiles, and the
whole thing fails silently at runtime -- as an UnsatisfiedLinkError on the first
wave, or worse, as a wave that arms against the wrong owner.

So this test derives the descriptor from the Java source and asserts the C++
export matches, rather than trusting that the two were edited together.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android_runtime_smoke/localapp/src/main/java/com/firstlight/offline/LocalBattleRuntime.java"
JNI = ROOT / "baseline/native_runner/probe/local_app_jni.inc"
PROBE = ROOT / "baseline/native_runner/probe/cr_replay_probe.cpp"
COLD_CLONE = ROOT / "baseline/native_runner/probe/cold_clone_adapter.inc"

JAVA_CLASS = "com_firstlight_offline_LocalBattleRuntime"

# The definition is wrapped across lines, so the exact signature text is
# brittle to reformat. Match on the distinctive prefix and the closing paren
# instead, anchored on the " {" that distinguishes the definition from the
# forward declaration above it.
ENQUEUE_SIGNATURE_PREFIX = "bool enqueue_local_scheduled_card(std::int32_t owner, std::uint32_t card_id,"


def _definition_body(text, start):
    """Body of the function whose signature starts at `start`, or None."""
    paren = text.index("(", start)
    depth = 0
    index = paren
    while True:
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
        index += 1
    cursor = index + 1
    while text[cursor] in "; \t\n":
        cursor += 1
    if text[cursor] != "{":
        return None  # a forward declaration, not a definition
    brace = cursor
    depth = 0
    while True:
        char = text[brace]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:brace + 1]
        brace += 1


def function_body(text, signature_prefix):
    """The statements of the function *defined* with that signature prefix.

    Both functions inspected here have a forward declaration above the
    definition. Scanning from the first occurrence would return the declaration,
    which has no statements, so every occurrence is tried and the one that is a
    definition wins. Matching the prefix rather than the whole signature also
    keeps this independent of how the parameter list is wrapped.
    """
    offset = 0
    while True:
        start = text.find(signature_prefix, offset)
        if start < 0:
            raise AssertionError(f"no definition found for {signature_prefix!r}")
        body = _definition_body(text, start)
        if body is not None:
            return body
        offset = start + 1

# Java source type -> JNI type descriptor letter. Only the types this bridge
# actually uses need a mapping.
JNI_TYPES = {
    "boolean": "Z",
    "int": "I",
    "long": "J",
    "String": "Ljava/lang/String;",
    "void": "V",
}


def strip_c_comments(text):
    """Remove // and /* */ comments, string and char literals.

    A naive `//[^\\n]*` would eat `JNIEnv*` (in `JNIEnv*/jclass`) and the `//`
    inside the fully-qualified Java class name, which is exactly the text these
    assertions are looking for.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "//":
            while i < n and text[i] != "\n":
                i += 1
        elif two == "/*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
        elif text[i] in "\"'":
            quote = text[i]
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
            i += 1
            out.append(" ")
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def java_native_declarations():
    """Yield (name, [java types], return type) for every native method."""
    text = strip_c_comments(JAVA.read_text(encoding="utf-8"))
    pattern = re.compile(r"\bnative\s+(\w+)\s+(\w+)\s*\(([^)]*)\)\s*;", re.MULTILINE)
    for return_type, name, raw_params in pattern.findall(text):
        params = [item.strip() for item in raw_params.split(",") if item.strip()]
        types = [item.rsplit(" ", 1)[0].strip() for item in params]
        yield name, types, return_type


def java_descriptor(return_type, param_types):
    for item in list(param_types) + [return_type]:
        if item not in JNI_TYPES:
            raise AssertionError(f"unmapped Java type in native signature: {item!r}")
    return "(" + "".join(JNI_TYPES[item] for item in param_types) + ")" + JNI_TYPES[return_type]


def cpp_export_signatures():
    """Map exported short name -> the C++ parameter list that follows it."""
    text = strip_c_comments(JNI.read_text(encoding="utf-8"))
    found = {}
    for name, raw in re.findall(
        r"Java_%s_(\w+)\s*\(([^)]*)\)\s*\{" % JAVA_CLASS, text, re.DOTALL
    ):
        params = [item.strip() for item in raw.split(",") if item.strip()]
        found[name] = params
    return found


def cpp_body(name):
    """The statements of one exported C++ function, comments already removed."""
    text = strip_c_comments(JNI.read_text(encoding="utf-8"))
    marker = "Java_%s_%s(" % (JAVA_CLASS, name)
    start = text.index(marker)
    depth = 0
    index = start
    while True:
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
        index += 1


class JniScheduleCardTest(unittest.TestCase):
    def setUp(self):
        self.declarations = {name: (types, ret) for name, types, ret in java_native_declarations()}
        self.exports = cpp_export_signatures()

    def test_java_declares_schedule_card(self):
        self.assertIn("scheduleCard", self.declarations,
                      "LocalBattleRuntime.java has no scheduleCard native declaration")

    def test_schedule_card_signature(self):
        types, return_type = self.declarations["scheduleCard"]
        self.assertEqual(types, ["int", "int", "int", "int", "int", "int"])
        self.assertEqual(return_type, "String")
        self.assertEqual(java_descriptor(return_type, types), "(IIIIII)Ljava/lang/String;")

    def test_cpp_exports_schedule_card(self):
        self.assertIn("scheduleCard", self.exports,
                      "local_app_jni.inc exports no scheduleCard symbol")

    def test_cpp_export_arity_matches_the_java_descriptor(self):
        # The C++ parameter list is the part a rename can silently desync.
        params = self.exports["scheduleCard"]
        # JNIEnv*, jclass, then one jint per Java parameter.
        self.assertEqual([p.replace(" ", "") for p in params[:2]], ["JNIEnv*env", "jclass"])
        self.assertEqual([p.split()[0] for p in params[2:]], ["jint"] * 6)
        self.assertEqual(len(params) - 2, len(self.declarations["scheduleCard"][0]))

    def test_cpp_parameter_order_matches_the_java_order(self):
        # A transposed x/y is a valid C++ signature and a silently wrong wave.
        # `level` is last: it is the newest parameter, so a reader who checks this
        # list against the wave table's own field order sees it trailing the tick.
        self.assertEqual(
            [p.split()[1] for p in self.exports["scheduleCard"][2:]],
            ["owner", "card_id", "x", "y", "execute_tick", "level"],
        )

    def test_every_java_native_has_a_cpp_export(self):
        # Broad guard on the same failure mode across the whole bridge.
        missing = sorted(name for name in self.declarations if name not in self.exports)
        self.assertEqual(missing, [],
                         f"Java declares natives with no C++ export: {missing}")

    def test_schedule_card_enqueues_into_the_engine_queue(self):
        # scheduleCard must reach the engine's own scheduled-action queue so the
        # per-tick hook performs the deploy, rather than re-implementing a deploy.
        probe = PROBE.read_text(encoding="utf-8")
        self.assertIn("bool enqueue_local_scheduled_card(", probe)
        self.assertIn("enqueue_local_scheduled_card(", cpp_body("scheduleCard"))
        # And the engine must already be draining that queue on the local path.
        self.assertIn("process_scheduled_replay_actions(manager);", probe)

    def test_schedule_card_does_not_nest_the_two_locks(self):
        # process_scheduled_replay_actions takes g_control_mutex from the game
        # thread. If the JNI entry point took g_local_mutex and then called
        # through, the two lock orders could deadlock.
        body = cpp_body("scheduleCard")
        self.assertNotIn("g_local_mutex", body)
        self.assertNotIn("g_control_mutex", body)

    def test_schedule_card_only_registers_a_pending_card_action(self):
        # It must be a Card action carrying the card id, or the engine resolves
        # it as an ability and the wave never deploys.
        body = cpp_body("scheduleCard")
        self.assertIn("enqueue_local_scheduled_card(", body)

        enqueue = LocalAppIdentityTest()._enqueue_body()
        self.assertIn("scheduled.kind = ScheduledReplayActionKind::Card;", enqueue)
        self.assertIn("scheduled.card_id = card_id;", enqueue)
        self.assertIn("scheduled.target_execute_tick = target_execute_tick;", enqueue)
        # The engine marks it Pending by way of the `scheduled = {}` reset.
        self.assertIn("scheduled = {};", enqueue)
        # ...and requires an execute tick strictly in the future.
        self.assertIn("if (target_execute_tick <= current_tick)", enqueue)


class LocalAppIdentityTest(unittest.TestCase):
    """The enqueue path and the consumer must recognise the same battle.

    Found on the device: every wave returned "no live battle" and the match ran
    to completion with no monsters at all. The local offline app arms its battle
    through its own manager (`g_local_native_manager`) and its own generation
    counter, and never populates the headless / native-render capture identity
    that `observation_capture_identity_locked()` reads -- for the local app that
    identity is permanently {manager=nullptr, generation=0, state_epoch=0}.

    So both ends must be taught about the local identity, and they must agree on
    it. If only the enqueue path is fixed the wave is accepted and then skipped
    on every tick; if only the consumer is fixed the wave is refused. Neither
    failure is visible without a device, which is why it is pinned here.
    """

    @staticmethod
    def probe():
        return strip_c_comments(PROBE.read_text(encoding="utf-8"))

    def _enqueue_body(self):
        return function_body(self.probe(), ENQUEUE_SIGNATURE_PREFIX)

    def _consume_body(self):
        return function_body(self.probe(), "void process_scheduled_replay_actions(void *manager)")

    def test_the_local_battle_predicate_exists(self):
        self.assertIn("bool local_app_live_manager_matches(void *manager)", self.probe())

    def test_the_predicate_checks_the_local_manager_and_generation(self):
        body = self.probe().split("bool local_app_live_manager_matches(void *manager) {", 1)[1]
        body = body.split("\n}", 1)[0]
        self.assertIn("g_local_running", body)
        self.assertIn("g_local_native_manager", body)
        self.assertIn("g_local_generation", body)

    def test_enqueue_stamps_the_local_identity(self):
        body = self._enqueue_body()
        self.assertIn("local_app_live_manager_matches", body)
        self.assertIn("scheduled.manager = action_manager;", body)
        self.assertIn("scheduled.generation = action_generation;", body)
        self.assertIn("scheduled.state_epoch = action_state_epoch;", body)
        # The stale identity must never be written onto a local-app wave.
        self.assertNotIn("scheduled.manager = identity.manager;", body)

    def test_consumer_accepts_the_local_battle(self):
        body = self._consume_body()
        self.assertIn("local_app_live_manager_matches", body)
        self.assertIn("current_tick = g_game_state_tick(manager);", body)

    def test_consumer_matches_on_the_same_triple_the_enqueue_stamped(self):
        # manager / generation / state_epoch are what the consumer compares, so
        # they must come from the local identity, not the published one.
        body = self._consume_body()
        self.assertIn("if (scheduled.manager != action_manager)", body)
        self.assertIn("scheduled.generation != action_generation", body)
        self.assertIn("scheduled.state_epoch != action_state_epoch", body)
        self.assertNotIn("scheduled.manager != identity.manager", body)
        self.assertNotIn("scheduled.generation != identity.generation", body)

    def test_both_sides_use_the_local_generation_as_the_state_epoch(self):
        # A new battle bumps g_local_generation, which is what invalidates a wave
        # queued against the previous scene. If the two sides derived the epoch
        # differently, every wave would fail GenerationChanged at execute time.
        enqueue = self._enqueue_body()
        consume = self._consume_body()
        self.assertIn("action_state_epoch = g_local_generation;", enqueue)
        self.assertIn("action_state_epoch = g_local_generation;", consume)
        self.assertIn("action_generation = g_local_generation;", enqueue)
        self.assertIn("action_generation = g_local_generation;", consume)

    def test_headless_and_native_render_paths_are_preserved(self):
        # The local identity is an addition, not a replacement.
        enqueue = self._enqueue_body()
        consume = self._consume_body()
        self.assertIn("identity.mode == RunnerMode::Headless", enqueue)
        self.assertIn("identity.mode == RunnerMode::NativeRender", enqueue)
        self.assertIn("identity.mode == RunnerMode::Headless", consume)
        self.assertIn("identity.mode == RunnerMode::NativeRender", consume)

    def test_the_consumption_hook_still_runs_on_the_local_path(self):
        # The local app advances the world through the hooked owner update, not
        # through game_state_step_hook, so the per-tick consumer has to be
        # driven from there as well. Found on the device: all eight waves armed
        # successfully ("localApp":true) and not one monster appeared, because
        # nothing was ever calling the consumer on this path.
        adapter = COLD_CLONE.read_text(encoding="utf-8")
        self.assertIn("process_scheduled_replay_actions(g_local_native_manager);", adapter)

    def test_the_local_pump_only_drives_the_consumer_while_a_battle_runs(self):
        # It must sit inside the same running-battle guard as the other local
        # work, not on every frame of the home screen.
        adapter = COLD_CLONE.read_text(encoding="utf-8")
        guard = adapter.split(
            "if(g_local_running.load(std::memory_order_acquire) && g_local_native_manager", 1)[1]
        self.assertIn("process_scheduled_replay_actions(g_local_native_manager);", guard)
        # Before the capture backlog wait, so a wave fires before the frame that
        # would show it is handed to the policy.
        self.assertLess(guard.index("process_scheduled_replay_actions"),
                        guard.index("g_local_frames.size()>=2"))

    def test_the_step_hook_still_owns_the_engine_paths(self):
        # The local-app addition must not replace the engine's own call sites.
        # There are two by design: the native-render path and the headless path.
        probe = self.probe()
        self.assertEqual(probe.count("process_scheduled_replay_actions(manager);"), 2)
        self.assertEqual(probe.count("process_scheduled_replay_actions(g_local_native_manager);"), 0)


if __name__ == "__main__":
    unittest.main()
