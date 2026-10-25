# Public STP preservation audit

Both public STP sync entry points now use the strict quote-aware parser. Existing
conversion, read, and parse failures propagate before the writer is called.
Default sections have comma separators and externally supplied sections are
validated before use. On-save checksum refresh occurs before binary sync so an
STP failure cannot leave corrected HSL with a stale checksum. MED may already be
updated when STP fails; these files are not one atomic transaction.

Measured precommit suite: 217 PASS, exit 0, watchdog 12.1 seconds. The earlier
216-case run retained one failure from an invalid legacy test fixture; that
fixture now contains valid separated tokens and asserts exact preservation.
Seven new tests cover both APIs, conversion/read/parse failures, no overwrite,
temporary cleanup, selected GUID preservation, actual native sibling conversion,
and an actual MED write followed by an STP failure with an unchanged checksum on
the second checksum pass. All files used by the native converter are synthetic.

Remaining: postcommit rerun and independent review; prior encoding/variant review
coverage remains separate. Only the supported minimal Properties/header grammar
is accepted; unsupported forms fail closed. No physical qualification is claimed.

Encoding/variant follow-up: the native converter independently mapped both
`\0x09` and `\t` to byte 09, and `\\t` to bytes 5c74. The Python text decoder
now accepts native TAB output. Empty converter overrides produce an explicit
configuration error; unsupported Unicode is rejected before container writing.
The full expanded precommit suite passed 223 cases in a 54.3-second watchdog run,
including all registered general step variants, all device stubs and both STP
default builders, Latin-1/control/TAB tokens, and an actual truncated binary
conversion rejection. The initial six-case boundary run retained three failures;
its incorrect unknown-escape expectation was corrected using captured native
binary bytes, not the primary implementation. No speed improvement is claimed.

R4-R8 follow-up in progress: inspected the actual Claude findings at de03b0b.
Adding populated raw TAB/C0/Latin-1 generator and sync cases before changing
escaping, direct writer and sync Unicode rejection/cleanup, default-STP GUID
parity, and populated multi-block/return-value serialization. Existing owner
out/*.js edits remain untouched. Native conversion uses synthetic temporary
fixtures only; no HSL execution or device semantics are claimed.

R4-R8 measured baseline: 9 new tests, 8 PASS / 1 FAIL, 12645.1119 ms summed
elapsed, 328.125 ms worker CPU, 50036736 B worker peak RSS. All eight raw-character
builder variants and eight full-sync variants preserved TAB, byte 0x01, and
é/µ in decoded native binary and text. No raw-character escaping fix was needed.
The sibling default STP builder incorrectly decoded a literal backslash-t GUID
token as TAB; it now uses the shared _escape_string. Both Python builders retain
the literal backslash header under their existing grammar, while the native
converter rejects such a header as unsupported. The initial test's native-header
assumption and the actual sibling-token defect remain in the baseline evidence;
the permanent tests separate those two contracts. Quoted headers fail closed.

Direct writer tests now reach encoding failure with existing and absent targets,
and verify complete temporary cleanup. Full sync preserves HSL/MED/STP originals
on non-Latin-1 rejection; on-save preserves binary companions and surfaces the
error, with its documented earlier HSL checksum update. Nine device-default
token parity comparisons and five populated ReturnValue/Loop/If variants pass.
The pure text-emitter documentation now claims decoded-token equivalence, not
native spelling or byte identity. Corrected full precommit suite: 233/233 PASS,
zero skips, watchdog 66.3 seconds, exit 0. Evidence is under
LOCALAPPDATA/hamtk/audit-workers/med-populated-boundaries-20261024. Native converter
SHA256: 35e5a5b471d41ca6390607421866e5092a31d09da764448ccb2f34d061b17dec.
Worker CPU/RSS exclude native child usage. Next: scoped commit and full postcommit
measurement; parent owns independent review. No performance or physical claim.
