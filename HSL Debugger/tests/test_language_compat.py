import os
import sys
import tempfile
import unittest


DEBUGGER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DEBUGGER_ROOT not in sys.path:
    sys.path.insert(0, DEBUGGER_ROOT)

from hsl_runtime.ast_nodes import FunctionDeclaration, LockStatement, UnlockStatement
from hsl_runtime.interpreter import HslRuntimeError, Interpreter, TraceOutput
from hsl_runtime.lexer import Lexer
from hsl_runtime.parser import ParseError, Parser
from hsl_runtime.preprocessor import Preprocessor, PreprocessorError


def parse(source, strict=True):
    parser = Parser(Lexer(source, "compat.hsl").tokenize(), "compat.hsl", strict=strict)
    return parser, parser.parse()


class HslLanguageCompatibilityTests(unittest.TestCase):
    def test_synchronized_lock_and_unlock_are_real_constructs(self):
        parser, program = parse(
            "synchronized function Guard() void { lock; unlock; }"
        )
        self.assertEqual([], parser.errors)
        declaration = program.declarations[0]
        self.assertIsInstance(declaration, FunctionDeclaration)
        self.assertTrue(declaration.is_synchronized)
        self.assertIsInstance(declaration.body.statements[0], LockStatement)
        self.assertIsInstance(declaration.body.statements[1], UnlockStatement)

    def test_onerror_resume_next_and_activity_execute_sequentially(self):
        _, program = parse(
            "method main() void {"
            " variable x(0);"
            " onerror resume next;"
            " err.Raise(7, \"expected\", \"test\");"
            " x = 2;"
            " activity(0) { x = x + 1; }"
            " Trace(IStr(x));"
            "}"
        )
        trace = TraceOutput(verbose=False)
        Interpreter(trace=trace, strict=True).execute(program)
        self.assertIn("3", trace.messages)
        self.assertTrue(any("onerror resume next" in message for message in trace.messages))

    def test_workflow_is_an_executable_scheduler_entry_point(self):
        _, program = parse('workflow Auto() void { Trace("workflow"); }')
        trace = TraceOutput(verbose=False)
        interpreter = Interpreter(trace=trace, strict=True)
        interpreter.execute(program)
        self.assertEqual("scheduler", interpreter.execution_mode)
        self.assertIn("workflow", trace.messages)

    def test_standalone_next_is_rejected(self):
        with self.assertRaises(ParseError):
            parse("method main() void { next; }")

    def test_strict_preprocessor_rejects_missing_include(self):
        with tempfile.TemporaryDirectory() as directory:
            method = os.path.join(directory, "missing.hsl")
            with open(method, "w", encoding="ascii") as stream:
                stream.write('#include "does-not-exist.hsl"\nmethod main() void {}\n')
            with self.assertRaises(PreprocessorError):
                Preprocessor(hamilton_dir=directory, strict=True).preprocess_file(method)

    def test_commented_include_examples_are_not_executed(self):
        source = (
            '// Usage: #include "not-a-real-file.hsl"\n'
            '/* #include "also-not-real.hsl" */\n'
            'method main() void {}\n'
        )
        processed = Preprocessor(strict=True).preprocess(source, "comments.hsl")
        self.assertIn('method main() void {}', processed)

    def test_failed_include_does_not_leave_stale_stack_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = os.path.join(directory, "bad.hsl")
            good = os.path.join(directory, "good.hsl")
            with open(bad, "w", encoding="ascii") as stream:
                stream.write('#include "missing.hsl"\n')
            with open(good, "w", encoding="ascii") as stream:
                stream.write('method main() void {}\n')
            preprocessor = Preprocessor(hamilton_dir=directory, strict=True)
            with self.assertRaises(PreprocessorError):
                preprocessor.preprocess_file(bad)
            self.assertEqual([], preprocessor.include_stack)
            self.assertIn('method main() void {}', preprocessor.preprocess_file(good))

    def test_guarded_self_include_is_processed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            guarded = os.path.join(directory, "guarded.hsl")
            with open(guarded, "w", encoding="ascii") as stream:
                stream.write(
                    '#ifndef __GUARDED_HSL__\n'
                    '#define __GUARDED_HSL__ 1\n'
                    '#include "guarded.hsl"\n'
                    'method main() void {}\n'
                    '#endif\n'
                )
            processed = Preprocessor(
                hamilton_dir=directory, strict=True
            ).preprocess_file(guarded)
            self.assertEqual(1, processed.count('method main() void {}'))

    def test_active_unguarded_include_reentry_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "first.hsl")
            second = os.path.join(directory, "second.hsl")
            with open(first, "w", encoding="ascii") as stream:
                stream.write('#include "second.hsl"\nmethod main() void {}\n')
            with open(second, "w", encoding="ascii") as stream:
                stream.write('#include "first.hsl"\nfunction Helper() void {}\n')
            processed = Preprocessor(
                hamilton_dir=directory, strict=True
            ).preprocess_file(first)
            self.assertEqual(1, processed.count('method main() void {}'))
            self.assertEqual(1, processed.count('function Helper() void {}'))

    def test_strict_runtime_rejects_unknown_calls(self):
        _, program = parse("method main() void { DoesNotExist(); }")
        with self.assertRaises(HslRuntimeError):
            Interpreter(trace=TraceOutput(verbose=False), strict=True).execute(program)

    def test_strict_parser_rejects_unimplemented_struct_values(self):
        with self.assertRaises(ParseError):
            parse("struct Point { variable x; variable y; };")

    def test_strict_parser_enforces_declarations_before_statements(self):
        with self.assertRaises(ParseError):
            parse('method main() void { Trace("first"); variable late; }')

    def test_function_definitions_may_follow_other_definitions_in_namespace(self):
        parser, _ = parse(
            "namespace Library {"
            " function First() void {}"
            " function Second() void {}"
            "}"
        )
        self.assertEqual([], parser.errors)

    def test_value_and_reference_parameters_have_distinct_semantics(self):
        _, program = parse(
            "function ByValue(variable value) void { value = 2; }"
            "function ByReference(variable& value) void { value = 3; }"
            "method main() void {"
            " variable value(1);"
            " ByValue(value);"
            " Trace(IStr(value));"
            " ByReference(value);"
            " Trace(IStr(value));"
            "}"
        )
        trace = TraceOutput(verbose=False)
        Interpreter(trace=trace, strict=True).execute(program)
        numeric_messages = [message for message in trace.messages if message in ("1", "3")]
        self.assertEqual(["1", "3"], numeric_messages)

    def test_logical_operators_short_circuit(self):
        _, program = parse(
            'method main() void { variable value(0); if (0 && DoesNotExist()) { value = 1; } Trace(IStr(value)); }'
        )
        trace = TraceOutput(verbose=False)
        Interpreter(trace=trace, strict=True).execute(program)
        self.assertIn("0", trace.messages)

    def test_string_constructor_initializer_is_preserved(self):
        _, program = parse('method main() void { string value("Hamilton"); Trace(value); }')
        trace = TraceOutput(verbose=False)
        Interpreter(trace=trace, strict=True).execute(program)
        self.assertIn("Hamilton", trace.messages)


if __name__ == "__main__":
    unittest.main()
