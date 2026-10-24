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
