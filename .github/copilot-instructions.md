# Hamilton HSL -- Pipetting Step Reference

This VS Code extension supports Hamilton Standard Language (HSL) files for Hamilton liquid-handling robots. This document explains how pipetting steps are encoded in HSL code and companion `.stp` files so that an LLM can interpret, validate, and explain them.

---

## ABSOLUTE RULE: No Em Dashes or En Dashes (Zero Tolerance)

**NEVER** use em dashes (`U+2014` --) or en dashes (`U+2013` --) in **any** file touched by this extension or workspace. This applies to **every** file type: `.hsl`, `.sub`, `.stp`, `.med`, `.json`, `.ts`, `.js`, `.md`, `.py`, `.ps1`, `.csv`, `.txt`, or any other format. No exceptions.

- Use **two hyphens** (`--`) where you would use an em dash.
- Use a **single hyphen** (`-`) where you would use an en dash.
- This rule applies to code, comments, strings, documentation, commit messages, and any generated or modified text.

HSL and the VENUS toolchain do **not** handle Unicode dashes correctly. Inserting an em dash or en dash into any file can cause silent corruption, compile failures, or runtime errors. **There is no valid use case for these characters in this workspace.**

More broadly, **all non-ASCII characters** (smart quotes, arrows, non-breaking spaces, accented letters, etc.) should be avoided in `.hsl`, `.sub`, and related files. The VENUS compiler is a Win32 application that reads source files as ANSI/Windows-1252. UTF-8 multi-byte sequences (e.g., em dash U+2014 = bytes `E2 80 94`) are misinterpreted: byte `0x94` becomes a right double quotation mark in Windows-1252, corrupting the parser state and producing cascading syntax errors far from the actual offending character.

The VS Code extension enforces this rule with diagnostic code `non-ascii-character`. Any character outside printable ASCII (`0x20`-`0x7E`), tab, carriage return, or line feed is flagged as an error -- even inside comments and strings, because VENUS processes the raw bytes before parsing.

### Optional Replacement on Save

When `hsl.autoSanitizeNonAscii` is explicitly enabled, saving replaces known non-ASCII characters with ASCII equivalents. Any remaining characters without a mapping are stripped. The setting is disabled by default because it changes source text; diagnostics remain active either way.

The following replacements are performed when the setting is enabled:

| Non-ASCII Character | Replacement | Description |
|---|---|---|
| `U+2014` (em dash) | `-` | Em dash to hyphen |
| `U+2013` (en dash) | `-` | En dash to hyphen |
| `U+00B5` (micro sign) | `u` | Mu/micro to lowercase u |
| `U+03BC` (Greek mu) | `u` | Greek mu to lowercase u |
| `U+2192` (right arrow) | `->` | Right arrow |
| `U+2190` (left arrow) | `<-` | Left arrow |
| `U+2194` (left-right arrow) | `<->` | Bidirectional arrow |
| `U+21D2` (double right arrow) | `=>` | Double right arrow |
| `U+21D0` (double left arrow) | `<=` | Double left arrow |
| `U+2018` (left single quote) | `'` | Smart single quote |
| `U+2019` (right single quote) | `'` | Smart single quote |
| `U+201C` (left double quote) | `"` | Smart double quote |
| `U+201D` (right double quote) | `"` | Smart double quote |
| `U+00A0` (non-breaking space) | ` ` | Non-breaking space to regular space |
| `U+2026` (ellipsis) | `...` | Horizontal ellipsis |
| `U+00D7` (multiplication sign) | `x` | Multiplication sign |
| `U+00F7` (division sign) | `/` | Division sign |
| `U+2264` (less-than or equal) | `<=` | Math operator |
| `U+2265` (greater-than or equal) | `>=` | Math operator |
| `U+2260` (not equal) | `!=` | Math operator |
| All other non-ASCII | *(removed)* | Stripped to prevent corruption |

### LLM-Specific Directive (GitHub Copilot, ChatGPT, Claude, etc.)

**This instruction is directed at you, the language model.** When generating, editing, or suggesting ANY text that will be written to a file in this workspace -- whether code, comments, strings, markdown, JSON, or any other format -- you MUST NOT produce any non-ASCII character. This is not a style preference; it is a hard technical constraint. Violations cause real compile failures and silent data corruption in the Hamilton VENUS toolchain. Zero exceptions.

**Specifically forbidden characters and their required ASCII replacements:**

| Forbidden | Use Instead |
|---|---|
| Em dash (`U+2014`) | `-` (single hyphen) |
| En dash (`U+2013`) | `-` (single hyphen) |
| Micro sign / Greek mu (`U+00B5`, `U+03BC`) | `u` |
| Right arrow (`U+2192`) | `->` |
| Left arrow (`U+2190`) | `<-` |
| Left-right arrow (`U+2194`) | `<->` |
| Double right arrow (`U+21D2`) | `=>` |
| Double left arrow (`U+21D0`) | `<=` |
| Smart single quotes (`U+2018`, `U+2019`) | `'` (ASCII apostrophe) |
| Smart double quotes (`U+201C`, `U+201D`) | `"` (ASCII double quote) |
| Non-breaking space (`U+00A0`) | ` ` (regular space) |
| Horizontal ellipsis (`U+2026`) | `...` (three periods) |
| Multiplication sign (`U+00D7`) | `x` |
| Division sign (`U+00F7`) | `/` |
| Less-than or equal (`U+2264`) | `<=` |
| Greater-than or equal (`U+2265`) | `>=` |
| Not equal (`U+2260`) | `!=` |
| Any other non-ASCII character | Remove entirely or use closest ASCII equivalent |

If your default text generation inserts typographic characters (smart quotes, em dashes, arrows, etc.), you must actively replace them with the ASCII equivalents listed above before outputting. Every character in your output must be within the printable ASCII range (`0x20`-`0x7E`), tab, carriage return, or line feed. Nothing else.

---

## ABSOLUTE RULE: x86 (32-bit) Architecture Only (Zero Tolerance)

Hamilton VENUS, the HSL compiler, the HSL runtime (`HxRun.exe`), and **all** Hamilton COM objects are **32-bit (x86) applications**. They are installed under `C:\Program Files (x86)\Hamilton`. There is no 64-bit version. There never has been. Every tool, script, build command, COM registration, and PowerShell invocation in this workspace **MUST** target x86. Using x64 (64-bit) for any of these operations will cause silent failures, COM class-not-found errors, `RegAsm` registration that VENUS cannot see, or runtime crashes. **There is no valid use case for x64 in the Hamilton toolchain.**

### Why This Matters

On a 64-bit Windows system, 32-bit and 64-bit COM registrations are stored in **separate registry hives**. A COM DLL registered with the 64-bit `RegAsm.exe` (under `Framework64`) is invisible to any 32-bit process -- including VENUS. The DLL will appear registered (PowerShell's 64-bit `New-Object -ComObject` will find it), but VENUS and HSL will fail at runtime with "Class not registered" or similar errors. This is one of the most common and hardest-to-diagnose failures in Hamilton integrations.

Similarly, PowerShell on a 64-bit system defaults to the 64-bit host. COM objects instantiated in a 64-bit PowerShell session load from the 64-bit registry hive. If the COM server is 32-bit only (as all Hamilton COM servers are), the call will fail. You **must** use the 32-bit PowerShell host or ensure the COM object is registered for 32-bit consumption.

### Rules

1. **C# projects MUST target `x86`.** Set `<PlatformTarget>x86</PlatformTarget>` and `<Prefer32Bit>true</Prefer32Bit>` in every `.csproj` file. Never use `AnyCPU` without `Prefer32Bit` -- on a 64-bit OS, `AnyCPU` defaults to 64-bit, and the resulting assembly cannot interop with Hamilton's 32-bit COM objects. Never use `x64`.

2. **COM registration MUST use the 32-bit `RegAsm.exe`.** The correct path is:
   ```
   %SystemRoot%\Microsoft.NET\Framework\v4.0.30319\regasm.exe
   ```
   **NOT** `Framework64`. Using `Framework64\regasm.exe` registers the COM class in the 64-bit hive where VENUS cannot find it.

3. **`dotnet build` MUST specify the x86 platform.** When building .NET Framework projects:
   ```powershell
   dotnet build /p:Platform=x86
   ```
   or via MSBuild:
   ```powershell
   & "${env:SystemRoot}\Microsoft.NET\Framework\v4.0.30319\MSBuild.exe" /p:Platform=x86
   ```
   Never use the 64-bit MSBuild from `Framework64` for Hamilton-related projects.

4. **PowerShell COM interop MUST use a 32-bit process.** When instantiating Hamilton COM objects (or any COM object registered only in the 32-bit hive), launch the 32-bit PowerShell:
   ```powershell
   & "${env:SystemRoot}\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -Command {
       $obj = New-Object -ComObject HxCfgFilConverter.HxCfgFileConverterCOM
       # ... use the object ...
   }
   ```
   The default `powershell.exe` on a 64-bit system is the 64-bit host and **will not** find 32-bit-only COM classes.

5. **All Hamilton paths use `Program Files (x86)`.** VENUS installs to `C:\Program Files (x86)\Hamilton`. Never reference `C:\Program Files\Hamilton` -- that path does not exist.

6. **PyInstaller builds are architecture-neutral** for the HSL debugger (pure Python), so no x86 constraint there. But any Python code that calls `ctypes` to load Hamilton DLLs or access the Windows registry for Hamilton keys must target the 32-bit registry view (`winreg.KEY_WOW64_32KEY`).

### Common Mistakes That Break Everything

| Mistake | Symptom | Fix |
|---|---|---|
| `<PlatformTarget>AnyCPU</PlatformTarget>` without `Prefer32Bit` | COM interop fails on 64-bit OS | Set `<PlatformTarget>x86</PlatformTarget>` |
| `Framework64\regasm.exe` for COM registration | "Class not registered" in VENUS | Use `Framework\regasm.exe` (32-bit) |
| Default `powershell.exe` for COM access | "Retrieving the COM class factory failed" | Use `SysWOW64\WindowsPowerShell\v1.0\powershell.exe` |
| `dotnet build` without `/p:Platform=x86` | 64-bit DLL cannot load in 32-bit VENUS | Always pass `/p:Platform=x86` |
| Registering COM in 64-bit hive, testing in 64-bit PowerShell -- "works for me" | VENUS still cannot find the class | Register with 32-bit RegAsm; test in 32-bit PowerShell |

### LLM-Specific Directive (GitHub Copilot, ChatGPT, Claude, etc.)

**This instruction is directed at you, the language model.** When generating ANY code, script, build command, or terminal command that involves:
- Building C# projects in this workspace
- Registering COM objects
- Running `RegAsm.exe` or `MSBuild.exe`
- Invoking PowerShell to interact with COM objects
- Referencing Hamilton installation paths
- Creating or modifying `.csproj` files

You **MUST** target x86 (32-bit). This is not a preference -- it is a hard technical constraint. Using x64 will produce artifacts that silently fail when consumed by the Hamilton VENUS toolchain. If you are unsure whether something should be x86, **it should be x86**. Zero exceptions.

---

## How Pipetting Steps Appear in HSL Code

Device steps appear as function calls on a device object (typically `ML_STAR`). The format is:

```
ML_STAR._<CLSID>("stepInstanceGuid"); // StepName
```

- **CLSID**: A COM class identifier (underscore-formatted GUID) that identifies the **step type** (e.g., Aspirate, Dispense).
- **stepInstanceGuid**: A unique instance identifier that maps to the step's parameters stored in the companion `.stp` file.

The HSL code itself does **not** contain the pipetting parameters (volume, liquid class, LLD settings, etc.). All parameters are stored in the binary `.stp` file, keyed by the `stepInstanceGuid`.

### Step Type CLSIDs

| CLSID (underscore format) | Step Type |
|---|---|
| `1C0C0CB0_7C87_11D3_AD83_0004ACB1DCB2` | Initialize |
| `541143F5_7FA2_11D3_AD85_0004ACB1DCB2` | Aspirate (single channel) |
| `541143F8_7FA2_11D3_AD85_0004ACB1DCB2` | Dispense (single channel) |
| `541143FA_7FA2_11D3_AD85_0004ACB1DCB2` | TipPickUp (single channel) |
| `541143FC_7FA2_11D3_AD85_0004ACB1DCB2` | TipEject (single channel) |
| `54114400_7FA2_11D3_AD85_0004ACB1DCB2` | UnloadCarrier |
| `54114402_7FA2_11D3_AD85_0004ACB1DCB2` | LoadCarrier |
| `827392A0_B7E8_4472_9ED3_B45B71B5D27A` | Head96Aspirate (CO-RE 96) |
| `A48573A5_62ED_4951_9EF9_03207EFE34FB` | Head96Dispense (CO-RE 96) |
| `BD0D210B_0816_4C86_A903_D6B2DF73F78B` | Head96TipPickUp (CO-RE 96) |
| `2880E77A_3D6D_40FE_AF57_1BD1FE13960C` | Head96TipEject (CO-RE 96) |
| `EA251BFB_66DE_48D1_83E5_6884B4DD8D11` | MoveAutoLoad |
| `CC819D7A_5DD8_4d13_A921_D74A06460F9E` | GetPlate (iSWAP pickup) |
| `E34155E5_7529_4b6b_AE3E_CDDA40789D55` | PutPlate (iSWAP placement) |
| `86F668D0_478C_41b6_A78B_8B6B8EAA54A7` | ParkISwap (park iSWAP arm) |
| `A108628C_BEB7_4CB6_99FD_8523302C700F` | ZSwapGetPlate (CO-RE Grip pickup) |
| `9DF3DD4B_3B5E_4750_8989_04458D1B134B` | ZSwapPlacePlate (CO-RE Grip placement) |
| `8A76E9A2_8053_4a32_B36F_AD9556B09C99` | EasyiSWAPTransport (composite iSWAP move) |
| `7EF8970F_F753_4c75_AD76_0E0D9C2CC9CD` | EasyCOREGripTransport (composite CO-RE move) |
| `1FB5DA01_3ACB_11d4_AE1F_0004ACB1DCB2` | FirmwareCommand (raw firmware mnemonic) |
| `81DA4252_3BA9_11d4_AE21_0004ACB1DCB2` | MoveToPosition (move pipetting channels) |

---

## .stp File Structure

The `.stp` file is a binary HxCfgFile v3 container. Each step instance has a section keyed by its GUID containing token pairs (key, value). Parameters fall into two categories:

### Named String/Integer Fields

| Key | Type | Description |
|---|---|---|
| `1StepName` | string | Step type name (e.g., "Aspirate", "Head96Aspirate") |
| `1SequenceObject` | string | Target sequence (e.g., "ML_STAR.Cells") |
| `1SequenceName` | string | Sequence name (usually same as SequenceObject) |
| `1LiquidName` | string | Liquid class name. Empty on dispense means "same as aspirate" |
| `1ChannelPattern` | string | Channel enable mask (e.g., "11111111" for 8 channels) |
| `3SequenceCounting` | int | 0 = Manually, 1 = Automatic |
| `3LiquidFollowing` | int | 0 = Off, 1 = On (pipette tip follows liquid surface) |
| `3TipType` | int | Tip type identifier |
| `3DispenseMode` | int | 3 = Jet, 4 = Surface Empty, 5 = Drain Tip |
| `3UsePickUpPosition` | int | TipEject: 0 = Default, 1 = Pick-up position, 2 = Default waste |
| `3SameLiquid` | int | 1 = Use same liquid class as aspirate |

### Numeric Field IDs (inside parameter groups)

These fields are in a parenthesized group: `(-534183935 ...)` for CO-RE 96 head steps, or `(-534183936 ...)` for single-channel steps (repeated per channel).

| Field ID | Type | Parameter | Unit |
|---|---|---|---|
| `-534183924` | float | **Aspirate Volume** | µL |
| `-534183908` | float | **Dispense Volume** | µL |
| `-534183915` | float | **Mix Volume** | µL |
| `-534183914` | int | **Mix Cycles** | count |
| `-534183925` | float | **Mix Position from Liquid Surface** | mm |
| `-534183913` | float | **Submerge Depth** (position relative to liquid surface) | mm |
| `-534183919` | int | **LLD Mode**: 0 = Off, 1 = pLLD (Pressure), 5 = Capacitive | enum |
| `-534183928` | float | **LLD Sensitivity** | 1-5 scale |
| `-534183918` | float | **Pressure LLD Sensing** | -- |
| `-534183933` | int | **cLLD Sensitivity** | -- |
| `-534183622` | float | **Retract Distance from Surface** | mm |
| `-534183629` | float | **Side Touch** (0 = off) | -- |
| `-534183926` | float | **Retract Speed** | -- |
| `-534183920` | int | **Swap Speed** | -- |
| `-534183700` | float | **pLLD Sensitivity** | -- |
| `-534183876` | int | **Channel Enable** (1 = enabled) | bool |
| `-534183813` | int | **Touch Off Distance** | -- |
| `-534183909` | int | **Dispense Position Above Z-Start** | -- |

---

## How to Interpret a Pipetting Step

When you see a line like:
```hsl
arrRetValues = ML_STAR._827392A0_B7E8_4472_9ED3_B45B71B5D27A("89a59053_b35c_4f52_8b2411eb1cf98914"); // Head96Aspirate
```

1. **Identify the step type** from the CLSID: `827392A0...` = `Head96Aspirate`
2. **Look up the instance GUID** `89a59053...` in the `.stp` file
3. **Read the parameters**: sequence, liquid class, volume, mix settings, LLD mode, etc.

### Example Decoded Step

For the step above, the `.stp` file contains:
- **Sequence:** ML_STAR.Cells (counting: Manually)
- **Liquid Class:** HighVolumeFilter_96COREHead1000ul_Water_DispenseSurface_Empty
- **Aspirate Volume:** 150 µL
- **Mix:** 150 µL × 4 cycles
- **Submerge Depth:** 2 mm
- **LLD:** Capacitive, Sensitivity: 5
- **Liquid Following:** On

---

## Device Initialization and Hardware Access (Critical -- Methods vs Libraries)

HSL files fall into two categories with **different** device handling rules: **method files** (containing `method main()`) and **library files** (no `method main()`). The VS Code extension enforces these rules with distinct diagnostics for each category.

### Method Files (files with `method main()`)

Method files own the device lifecycle. They declare a `global device`, call Initialize, and then invoke device steps or call into library functions that use the device.

The **Initialize** step (`ML_STAR._1C0C0CB0_7C87_11D3_AD83_0004ACB1DCB2("guid")`) **must** be called inside `method main()` before **any** other instrument commands that use the device object (typically `ML_STAR`). This includes -- but is not limited to -- all pipetting steps (Aspirate, Dispense, TipPickUp, TipEject), carrier movements (LoadCarrier, UnloadCarrier, MoveAutoLoad), and CO-RE 96 head operations (Head96Aspirate, Head96Dispense, Head96TipPickUp, Head96TipEject).

Without the Initialize step the instrument hardware is **not** initialised. Every subsequent device command will fail at runtime.

**Method file rules:**

1. **One Initialize per `method main()`**: Place the Initialize call early in `method main()`, before running any `ML_STAR._*` device step.
2. **Declare `global device`**: Method files declare the device with a deck layout: `global device ML_STAR ("Layout.lay", "ML_STAR", hslTrue);`
3. **Omitting Initialize is a critical error**: The VS Code extension flags the first `ML_STAR` device call that appears before (or without) an Initialize step as an error (diagnostic code `missing-initialize-step`).
4. **The Hamilton Method Editor adds this automatically** when you graphically insert an "Initialize" step, but hand-written HSL must include it explicitly.

### Library Files (files without `method main()`)

Library files provide reusable functions that interact with the device hardware. They do **not** own the device -- they **receive** it from the calling method as a `device &` parameter. The calling method is responsible for initializing the device before invoking any library function that uses it.

**Library file rules:**

1. **Receive device as `device &` parameter**: Every library function that needs hardware access must accept the device via a `device &` parameter (e.g., `function MyStep(device & ML_STAR, sequence & i_seq) void`).
2. **Do NOT call Initialize**: The Initialize step belongs in `method main()` only. Calling Initialize in a library function is an error (diagnostic code `initialize-in-library`).
3. **Do NOT declare `global device`**: Library files should not contain `global device` declarations. The device is provided by the caller. The VS Code extension flags `global device` in a library file as a warning (diagnostic code `global-device-in-library`).
4. **Assume the device is already initialised**: Library functions can safely issue device commands because the calling method has already called Initialize.

### Examples

**Method file** (owns and initialises the device, calls library functions):

```hsl
#include "MyLibrary.hsl"

global device ML_STAR ("MyLayout.lay", "ML_STAR", hslTrue);

method main()
{
    variable arrRetValues[];

    // Initialize MUST come first
    arrRetValues = ML_STAR._1C0C0CB0_7C87_11D3_AD83_0004ACB1DCB2("62b7b0ef_8e71_4cd4_8763df32a80db666"); // Initialize

    // Direct device commands are safe after Initialize
    ML_STAR._541143FA_7FA2_11D3_AD85_0004ACB1DCB2("..."); // TipPickUp

    // Library calls -- device passed as parameter
    MyLib::DoSomething(ML_STAR, mySequence);
}
```

**Library file** (receives device, never initialises):

```hsl
namespace MyLib
{
    function DoSomething(device & ML_STAR, sequence & i_seq) void;

    function DoSomething(device & ML_STAR, sequence & i_seq) void
    {
        // Device is already initialised by the calling method.
        // Use it directly -- no Initialize call here.
        ML_STAR._541143F5_7FA2_11D3_AD85_0004ACB1DCB2("..."); // Aspirate
        ML_STAR._541143F8_7FA2_11D3_AD85_0004ACB1DCB2("..."); // Dispense
    }
}
```

### VS Code Extension Diagnostics

| Diagnostic Code | Severity | Applies To | Description |
|---|---|---|---|
| `missing-initialize-step` | Error | Method files | Device used before Initialize in `method main()` |
| `initialize-in-library` | Error | Library files | Initialize called in a file with no `method main()` |
| `global-device-in-library` | Warning | Library files | `global device` declared in a file with no `method main()` |
| `standalone-goto` | Error | All files | Standalone `goto <label>` used without `onerror` prefix |

---

## iSWAP & Transport Step Parameters (.stp File)

iSWAP and CO-RE Grip transport steps use a different set of STP parameters from pipetting steps. These are used for plate/rack pickup, placement, and iSWAP arm parking.

### Step Command Type IDs (field `-534118376`)

| Command ID | Step Type |
|---|---|
| 580 | GetPlate (iSWAP) |
| 581 | PutPlate (iSWAP) |
| 343 | ParkISwap |
| 441 | ZSwapGetPlate (CO-RE Grip) |
| 466 | ZSwapPlacePlate (CO-RE Grip) |
| 1041 | EasyiSWAPTransport |
| 1045 | EasyCOREGripTransport |
| 457 | FirmwareCommand (raw) |

### Named String/Integer Fields (iSWAP/Transport)

| Key | Type | Description |
|---|---|---|
| `1StepName` | string | Step type name (e.g., "GetPlate", "PutPlate", "ParkISwap") |
| `1PlateName` | string | Labware/sequence name for the plate or rack |
| `1PlateObject` | string | Labware/sequence object (usually same as PlateName) |
| `1GripperAccessHeight` | string | Variable name for grip height (e.g., "iSWAPGripHeight") |
| `5GripperAccessHeight` | float | Direct grip access height in mm |
| `1ToolSequenceName` | string | Gripper tool sequence name (CO-RE Grip) |
| `1ToolSequenceObject` | string | Gripper tool sequence object |
| `1ToolUpperChannel` | string | Upper channel number for CO-RE transport |
| `3InversGripping` | int | 0 = normal grip, 1 = inverse grip |
| `3GripperHotel` | int | 0 = no hotel parking |
| `3TransportMode` | int | 0 = standard |
| `3LabwareOrientation` | int | 0 = landscape, 1 = portrait |
| `3GripDirectionMode` | int | Grip approach direction (0, 1, 2) |
| `3GrippLargeSite` | int | 0 = normal site gripping |
| `3OverwriteGripWidth` | int | 2 = override grip width with custom value |
| `3ParkISwapAfterUse` | int | 0 = do not park, 1 = park after transport |
| `3CheckFreePositionFirst` | int | 0 = skip target check |
| `3SearchLabwareFirst` | int | 0 = skip labware search |
| `3ReadPlateBarcode` | int | 0 = skip barcode read during transport |
| `3AskForNoCollision` | int | 1 = collision avoidance prompt (ParkISwap) |
| `3ToolEject` | int | 0 = keep grippers, 1 = eject after use |
| `5ZSwapStrengthFactor` | float | CO-RE grip strength factor (0.5 typical) |
| `3ZSwapPlateCheck` | int | 0 = skip plate presence check |

### Numeric Field IDs (inside `-534183935` parameter group)

| Field ID | Type | Parameter | Unit |
|---|---|---|---|
| `-534183682` | float | **Z-travel height** | mm |
| `-534183683` | float | **Z-start / gripper access height** | mm |
| `-534183684` | float | **Z-end / clearance height** | mm |
| `-534183672` | float | **Z-position / plate height** | mm |
| `-534183694` | int | **Internal flag** | -- |
| `-534183723` | float | **Place precision / check flag** | -- |
| `-534183743` | float | **Z offset (source)** | mm |
| `-534183744` | float | **Z offset (target)** | mm |
| `-534183747` | float/str | **Grip opening width** | mm |
| `-534183773` | float | **Speed** | -- |
| `-534183774` | float/str | **Grip width** | mm |
| `-534183775` | int | **Grip force / tolerance** | -- |
| `-534183645` | int | **Place mode** | enum |
| `-534183815` | string | **Firmware command parameter string** (FirmwareCommand steps) | -- |
| `-534183816` | string | **Firmware command mnemonic** (e.g., "PXZI", "R0MO") | -- |

### iSWAP Firmware Mnemonics

These 4-character mnemonics are stored in STP field `-534183816` and sent to the instrument controller via the FirmwareCommand COM interface (`{1FB5DA01-3ACB-11d4-AE1F-0004ACB1DCB2}`).

| Mnemonic | Module | Description |
|---|---|---|
| `PXZI` | iSWAP motor controller | Prepare/position iSWAP traverse axis, initialize Z |
| `H0ZI` | iSWAP Z-axis | Home iSWAP Z-axis (retract gripper vertically) |
| `C0FY` | Channel/system | Coordination/synchronization (settle channel arm) |
| `R0MO` | iSWAP traverse | Move iSWAP to Opposite side (right -> left) |
| `R0MH` | iSWAP traverse | Move iSWAP to Home position (left -> right) |

### iSWAP Default Grip Parameters

| Rack Format | Grip Height | Grip Width | Grip Opening |
|---|---|---|---|
| 96-tip NTR | 25 mm | 77 mm | 90 mm |
| 384-tip NTR | 22 mm | 77 mm | 90 mm |

### CO-RE Grip Default Parameters

| Rack Format | Grip Height | Grip Width | Grip Opening | Z-Travel | Z-Position |
|---|---|---|---|---|---|
| 96-tip NTR | 21 mm | 79 mm | 86 mm | 277.8 mm | 128.7 mm |
| 384-tip NTR | 21 mm | 76 mm | 81 mm | 277.8 mm | 128.7 mm |

---

## Common Validation Checks

When reviewing pipetting steps, check for these potential issues:

1. **Volume out of range**: Aspirate/dispense volumes should be within the tip type's capacity (e.g., 0-1000 µL for 1000 µL tips, 0-5000 µL for 5 mL tips).
2. **Mix volume vs aspirate volume**: Mix volume should generally be ≤ the tip capacity and appropriate for the vessel.
3. **Mix cycles = 0 with non-zero mix volume**: Likely an error -- mix won't execute without cycles.
4. **LLD off with submerge depth**: If LLD is off, the submerge depth is relative to container bottom, not liquid surface. Verify this is intentional.
5. **Empty liquid class on aspirate**: Aspirate steps should always have a liquid class specified.
6. **Dispense volume ≠ aspirate volume**: Unless performing partial dispenses, these should usually match within a pipetting cycle.
7. **Sequence counting mismatch**: If a sequence is set to "Manually" but inside a loop without manual position management, positions won't advance.
8. **Liquid following without LLD**: Liquid following requires LLD to detect the surface; having it on without LLD is unusual though not always wrong.

---

## HSL Language Rules (Critical for Code Generation)

The following rules **must** be followed when generating or modifying HSL code. Violating any of these causes compile errors in the VENUS syntax checker.

### Type System -- `variable` vs `string`

HSL has distinct types: `variable`, `string`, `sequence`, `device`, `object`, `timer`, `event`, `file`, `resource`, `dialog`.

**`string` member functions** (`.GetLength()`, `.Find()`, `.Left()`, `.Mid()`, `.Right()`, `.Compare()`, `.MakeUpper()`, `.MakeLower()`, `.SpanExcluding()`) are available **only** on the `string` type. Calling them on a `variable` produces **VENUS error 1317**.

**`variable`** is a generic type that can hold numbers or text, but it does **not** have string member functions.

#### Rule: When you need string member functions, use `string` type

```hsl
// WRONG -- causes error 1317 on every member function call
variable strInput;
variable intLen;
intLen = strInput.GetLength();       // ERROR: GetLength is not a member of variable

// CORRECT -- use string type
string strInput;
variable intLen;
intLen = strInput.GetLength();       // OK: GetLength is a member of string
```

#### Rule: Converting `variable` to `string` for member function use

When a function receives a `variable` parameter but needs to use string member functions internally, declare a local `string` variable and assign:

```hsl
function MyFunction(variable i_strInput) void
{
   string strLocal;
   variable intLen;

   strLocal = i_strInput;                // assign variable to string
   intLen = strLocal.GetLength();        // now member functions work
}
```

This avoids changing the function's public signature while enabling string operations internally.

#### String member functions reference

| Method | Signature | Returns |
|---|---|---|
| `GetLength` | `str.GetLength()` | integer -- length of string |
| `Find` | `str.Find(searchStr)` | integer -- index of first occurrence, or -1 |
| `Left` | `str.Left(count)` | string -- leftmost `count` characters |
| `Mid` | `str.Mid(start, count)` | string -- substring from `start` for `count` chars |
| `Right` | `str.Right(count)` | string -- rightmost `count` characters |
| `Compare` | `str.Compare(other)` | integer -- <0, 0, or >0 (lexicographic) |
| `MakeUpper` | `str.MakeUpper()` | void -- converts to uppercase in place |
| `MakeLower` | `str.MakeLower()` | void -- converts to lowercase in place |
| `SpanExcluding` | `str.SpanExcluding(charSet)` | string -- prefix before any character in charSet |

### Sequence Member Functions

**`sequence.GetPositionId()`** takes **zero** arguments (VENUS error 1315 if called with arguments). You must first call `sequence.SetCurrentPosition(index)` to set the position, then call `GetPositionId()`:

```hsl
// WRONG -- GetPositionId takes 0 arguments
strId = mySeq.GetPositionId(intIndex);     // ERROR 1315

// CORRECT -- SetCurrentPosition first, then GetPositionId
mySeq.SetCurrentPosition(intIndex);
strId = mySeq.GetPositionId();
```

**`sequence.Add(labwareId, positionId)`** -- the first argument is the **labware ID**, the second is the **position ID**:

```hsl
// WRONG -- arguments are swapped
mySeq.Add("A1", "");           // puts "A1" as labwareId, "" as positionId

// CORRECT
mySeq.Add("", "A1");           // "" as labwareId, "A1" as positionId
```

### Function Visibility -- `private` Scope

**`private` functions can only be called from within the same file** (VENUS error 1343). If a helper file needs to call a function defined in another file, that function must **not** be `private`.

```hsl
// In LibraryA.hsl
namespace LibA
{
   private function _InternalOnly() void;    // only callable within LibraryA.hsl
   function SharedHelper() void;             // callable from any file that includes LibraryA.hsl
}

// In LibraryB.hsl -- after #include "LibraryA.hsl"
LibA::_InternalOnly();     // ERROR 1343 -- private function
LibA::SharedHelper();      // OK
```

Use the underscore prefix (`_FunctionName`) as a naming convention for internal functions, but only mark them `private` if they are truly file-local.

### Namespace `::` Operator -- Functions Only, Not Variables

The `::` scope-resolution operator can **only** be used to call functions across namespaces. It **cannot** be used to access global variables defined in another namespace. Attempting to read or write a variable through `Namespace::variableName` causes a compile error in VENUS.

The VS Code extension flags this with diagnostic code `namespace-qualified-variable`.

```hsl
namespace MyLib
{
   variable g_counter;

   function GetCounter() variable
   {
      return(g_counter);    // OK -- direct access within the same namespace
   }

   function SetCounter(variable val) void
   {
      g_counter = val;      // OK -- direct access within the same namespace
   }
}

// In another file or outside the namespace:
MyLib::GetCounter();          // OK -- function call via ::
MyLib::SetCounter(5);         // OK -- function call via ::
variable x;
x = MyLib::g_counter;         // ERROR -- cannot access variable via ::
MyLib::g_counter = 10;        // ERROR -- cannot access variable via ::
```

To access a namespace's internal state, expose getter/setter functions.

### Variable Declarations Must Be at Top of Scope

All type declarations (`variable`, `string`, `sequence`, `object`, etc.) must appear at the **beginning** of their enclosing scope, before any executable statements. This applies to every scope level: function bodies, method bodies, namespace blocks, and anonymous `{ }` blocks.

Anonymous blocks **are** allowed and can contain their own declarations -- as long as those declarations appear at the top of the block before any executable code. The Hamilton Method Editor generates this pattern for every device step:

```hsl
// Hamilton Method Editor-generated pattern -- VALID
method main()
{
   variable x;
   x = 5;
   {
      variable arrRetValues[];                        // OK -- declaration at top of block
      arrRetValues = ML_STAR._CLSID("guid");          // executable code follows
   }
}
```

```hsl
// WRONG -- declaration after executable code in the same scope
function Example() void
{
   variable x;
   x = 5;
   variable y;          // ERROR -- declaration after executable statement
   y = 10;
}

// WRONG -- declaration after executable code inside an anonymous block
method main()
{
   variable x;
   x = 5;
   {
      doSomething();    // executable code first
      variable y;       // ERROR -- declaration after executable code in this block
   }
}

// CORRECT -- all declarations at the top of each scope
function Example() void
{
   variable x;
   variable y;

   x = 5;
   y = x + 1;
}
```

### Array Element Assignment

Array element assignment using bracket notation (`arr[index] = value`) is valid HSL syntax **only when the array already has sufficient size**. Place **one assignment per line** for parser compatibility.

**Critical**: Declaring `variable arr[];` creates a **zero-length array**. You **cannot** grow it by assigning to indices (`arr[0] = "A"` causes runtime error `array reference out of bound` -- error `0x23 - 0x1 - 0x19`). This applies to both local arrays and arrays passed as function parameters.

Arrays can be populated by:
- Function return values: `arr = SomeFunction(...);`
- The runtime resizing the array internally (e.g., `DevAddLabware` populating a return array)
- Declaring with initial values in the Hamilton Method Editor

```hsl
// WRONG -- arr has zero length, index assignment fails at runtime
variable arrRows[];
arrRows[0] = "A";    // ERROR: array reference out of bound
arrRows[1] = "B";    // never reached

// CORRECT -- use if/else chain for small fixed lookups
variable strRow;
strRow = "A";
if (row == 1) { strRow = "B"; }
if (row == 2) { strRow = "C"; }
// ... etc.

// CORRECT -- populate from function return
variable arrRetValues[];
arrRetValues = ML_STAR._541143FA_7FA2_11D3_AD85_0004ACB1DCB2("guid"); // TipPickUp
```

When assignment is valid (pre-sized array), place **one assignment per line**:

```hsl
// AVOID -- multiple assignments on one line (may cause parser issues)
arrRows[0] = "A"; arrRows[1] = "B"; arrRows[2] = "C";

// PREFERRED -- one per line
arrRows[0] = "A";
arrRows[1] = "B";
arrRows[2] = "C";
```

### No Array Element Access in `+` Expressions

The VENUS parser **cannot** handle array element access (`arr[index]`) used directly as an operand in a `+` expression (concatenation or addition). The bracket notation confuses the parser and produces cascading "syntax error before ';'" errors (error 1002) followed by "unexpected end of file" (error 1311).

**Always** assign the array element to a temporary variable first, then use that variable in the expression.

The VS Code extension flags this with diagnostic code `array-element-in-expression`.

```hsl
// WRONG -- causes cascading syntax errors
strResult = strResult + arrPositions[intIdx];       // ERROR 1002
strResult = strResult + "," + arrData[i];            // ERROR 1002

// CORRECT -- extract to a temporary variable first
strPos = arrPositions[intIdx];
strResult = strResult + strPos;

strItem = arrData[i];
strResult = strResult + "," + strItem;
```

This restriction applies to **all** uses of `arr[index]` as an operand of `+`, including both left and right sides. The assignment `arr[index] = value` itself is fine -- only the use inside `+` expressions is prohibited.

### No Array Output Parameters in Library Functions

Passing a `variable arr[]` parameter to a library function and trying to grow it inside the function by index assignment (`arr[i] = value`) causes runtime error `array reference out of bound`. The caller's empty array cannot be resized through a parameter reference.

**Workaround**: Use deterministic naming conventions so the caller can reconstruct element names without an output array. For example, if a function creates labware IDs `Prefix_1`, `Prefix_2`, ..., `Prefix_N`, the caller can rebuild any ID with:

```hsl
// Library function -- no array output parameter needed
function AddPlateStack(
   device & ML_STAR,
   variable plateCount,
   variable labwareIdPrefix) variable
{
   variable i;
   variable labwareId;
   variable strIdx;

   i = 0;
   while (i < plateCount)
   {
      strIdx = IStr(i + 1);
      labwareId = labwareIdPrefix;
      labwareId = labwareId + "_";
      labwareId = labwareId + strIdx;
      // ... use labwareId ...
      i = i + 1;
   }
   return(plateCount);
}

// Caller -- reconstruct IDs using the naming convention
variable id;
variable strIdx;
i = 0;
while (i < addedCount)
{
   strIdx = IStr(i + 1);
   id = "AssayPlate_";
   id = id + strIdx;
   // ... use id ...
   i = i + 1;
}
```

### `DevGetLabwareData` Returns Strings

All values returned by `DevGetLabwareData()` (from HSLDevLib) are **strings**, even when the underlying property is numeric (e.g., `"Dim.Dz"`, `"StackHt"`, `"Dim.Dx"`). Using a returned value directly in a numeric comparison (`> 0`, `< 10`, etc.) causes runtime error `left hand side of expression is not a number` (error `0x23 - 0x2 - 0x4`).

**Always** wrap the return value with `FVal()` before numeric operations:

```hsl
// WRONG -- rawValue is a string, numeric comparison fails
variable rawValue;
rawValue = DevGetLabwareData(ML_STAR, labwareId, "Dim.Dz");
if (rawValue > 0) { ... }    // ERROR: not a number

// CORRECT -- convert with FVal() first
variable rawValue;
variable numValue;
rawValue = DevGetLabwareData(ML_STAR, labwareId, "Dim.Dz");
numValue = FVal(rawValue);
if (numValue > 0) { ... }    // OK
```

### No `continue` Keyword

HSL does **not** support a loop-continuation statement. Both C/C++ `continue;` and a standalone `next;` are invalid. `next` is reserved for the error-handling forms `resume next;` and `onerror resume next;`. Restructure the loop body with a condition or control flag:

```hsl
// WRONG
while(i < 10)
{
   i = i + 1;
   if(i == 5) continue;    // ERROR -- 'continue' not supported
   Trace(IStr(i));
}

// CORRECT -- guard the portion that should be skipped
while(i < 10)
{
   i = i + 1;
   if(i != 5)
   {
      Trace(IStr(i));
   }
}
```

### No Standalone `goto` Statement

HSL does **not** have a general-purpose `goto` keyword. Only `onerror goto <label>;` and `onerror goto 0;` are valid. A bare `goto <label>;` (without the preceding `onerror`) produces **VENUS error 1206**. The VS Code extension flags this with diagnostic code `standalone-goto`.

```hsl
// WRONG -- standalone goto does not exist in HSL
onerror goto _myErr;
DoSomethingRisky();
onerror goto 0;
goto _skipErr;       // ERROR 1206 -- 'goto' is not a valid statement

_myErr:
{
   err.Clear();
   onerror goto 0;
   resume next;
}
_skipErr:
```

### Error Handling Patterns (`onerror goto`)

HSL error handling uses `onerror goto <label>;` to register a jump target and `onerror goto 0;` to deregister it. Because standalone `goto` does not exist, you must structure error handlers so that **normal execution flow naturally reaches or bypasses the error-handler block** without a `goto`. Three patterns are available:

#### Pattern A: Fall-through (recommended when code continues after the risky call)

Both the success and error paths fall through into the label block. On success, `err.Clear()` is a no-op. This is the simplest and most common pattern.

```hsl
onerror goto _parkErr;
FW_ParkCarrierBackInDeck(ML_STAR, 1300, 200, 0);

_parkErr:
{
   err.Clear();
   onerror goto 0;
}
// Execution continues here regardless of success or failure
```

#### Pattern B: Return before label (when the error handler is the last thing in the function)

The success path exits via `return;` so it never reaches the label. The error handler is only entered on failure. Use `resume next;` inside the handler to continue after the failing statement (which resumes at the statement following the one that threw), or `return;` to exit the function from the handler.

```hsl
function DoWork(device & ML_STAR) void
{
   onerror goto _workErr;
   RiskyOperation();
   onerror goto 0;

   // ... more code ...

   return;

   _workErr:
   {
      Trace("Operation failed.");
      err.Clear();
      onerror goto 0;
      resume next;
   }
}
```

#### Pattern C: Flag variable (when you need to know whether the error occurred)

A flag variable is set after the risky call. Both paths fall through to the label, but the handler checks the flag to decide what to do.

```hsl
variable int_Ok;
int_Ok = 0;

onerror goto _checkErr;
RiskyCall();
int_Ok = 1;

_checkErr:
{
   if (int_Ok == 0)
   {
      Trace("RiskyCall failed -- continuing anyway.");
      err.Clear();
   }
   onerror goto 0;
}
```

#### Key rules

- **`onerror goto <label>;`** registers `<label>` as the error jump target.
- **`onerror goto 0;`** deregisters the current error handler. Always call this after the protected section to avoid catching unrelated errors.
- **`err.Clear();`** clears the active error inside a handler.
- **`resume next;`** continues execution at the statement after the one that threw. Only use inside an error handler entered via `onerror goto`.
- **Never use standalone `goto <label>;`** -- it does not exist in HSL.

### No Compound Assignment Operators

HSL does **not** support `+=`, `-=`, `*=`, `/=`. Use explicit assignment:

```hsl
// WRONG
x += 5;          // ERROR

// CORRECT
x = x + 5;
```

### Function Declaration and Definition Pairing

Every function must have both a forward **declaration** (prototype ending with `;`) and a **definition** (implementation with `{ ... }`). The signatures must match exactly (modifiers, name, parameter types, return type).

```hsl
// Forward declaration
function MyFunc(variable i_param) variable;

// Definition -- must match exactly
function MyFunc(variable i_param) variable
{
   return(i_param + 1);
}
```

---

## HSL Debugger -- Simulation Runtime

This workspace includes a **Python-based HSL simulation runtime** at `HSL Debugger/hsl_runtime/`. End users run its bundled executable, so Python, Hamilton hardware, VENUS, and Run Control are not required. Source files that include Hamilton's proprietary libraries still need those particular library files or an offline test harness that does not include them.

The debugger **requires `method main()`** in the target file. Library files without `method main()` will emit `"No main() method found"` and not execute. To test a library, create a test harness (see "Creating Test Harnesses" below).

### CRITICAL: PyInstaller Build Requirement (Read Before Modifying Python Code)

The Python runtime is **compiled into a standalone executable** using PyInstaller so that end users do **not** need Python installed. The VS Code extension ships and runs `HSL Debugger/dist/hsl_debugger/hsl_debugger.exe` -- it does **not** invoke `python` directly.

**If you modify ANY Python file under `HSL Debugger/hsl_runtime/`**, you **MUST** rebuild the executable afterward. The extension will not pick up your changes until you do.

#### How to rebuild

```powershell
cd "<workspace-root>/HSL Debugger"
pyinstaller --onedir --name hsl_debugger --console --noconfirm hsl_runtime/main.py
```

This produces `HSL Debugger/dist/hsl_debugger/hsl_debugger.exe` (plus supporting DLLs in the same folder). The `--onedir` flag creates a folder distribution (faster startup than `--onefile`). The `--console` flag is **required** so that stdout/stderr are piped to the VS Code debug adapter.

#### Build requirements

- Python 3.10+ with `pyinstaller` installed (`pip install pyinstaller`)
- The runtime uses **only Python standard library modules** (no pip packages) -- `os`, `re`, `sys`, `argparse`, `time`, `uuid`, `winreg`, `datetime`, `pathlib`, `typing`, `traceback`, `ctypes`

#### How the extension finds the exe

In `src/hslDebugAdapter.ts`, the `_handleDebugWithPython()` method checks for the bundled exe at:

```
<extension-root>/HSL Debugger/dist/hsl_debugger/hsl_debugger.exe
```

If found, it spawns that exe directly (no Python needed). If **not** found, it falls back to `python -m hsl_runtime.main` as a developer convenience -- but this fallback will fail for end users without Python.

#### Important: `shell: false`

The spawn call uses `shell: false` to avoid the Windows `cmd.exe` argument-splitting bug where paths containing spaces (e.g., `C:\Program Files (x86)\...`) are broken into multiple tokens. Do **not** change this to `shell: true`.

#### Files that should NOT be committed to git

- `HSL Debugger/build/` -- PyInstaller intermediate files
- `HSL Debugger/dist/` -- the built executable and supporting files
- `HSL Debugger/*.spec` -- PyInstaller spec files

These are listed in `.gitignore`. The `dist/` folder **is** included in the packaged `.vsix` extension (see `.vscodeignore`).

### What the Debugger Does

- Resolves `#include`, `#define`, and `#ifdef`/`#ifndef` directives
- Executes all HSL control flow, variable assignments, string/array/sequence operations
- Device steps (`ML_STAR._<CLSID>(...)`) return simulated success -- no firmware commands
- COM objects (e.g., `BarcodedTipTracking.Engine`) are simulated in Python
- `Trace()` / `FormatTrace()` output is captured and written to a `.trc` log file

### What the Debugger Does NOT Do

- No deck layout loading (`.lay` files not parsed)
- No `.stp` file reading (pipetting parameters not decoded)
- No firmware emulation (device steps are stubs returning success)

---

## LLM Execution Workflow -- Running and Validating HSL Code

The VS Code extension provides **two execution modes** for HSL files.

| Mode | Shortcut | Engine | Safety |
|------|----------|--------|--------|
| **Start Debugging** (safe run) | F5 | Python simulation runtime | Completely safe -- no hardware interaction |
| **Run Without Debugging** (real run) | Ctrl+F5 | Hamilton `HxRun.exe` | **Moves real hardware** if a robot is connected |

### Mandatory Execution Order

**Always run Start Debugging (Python simulation) first**, then Run Without Debugging (HxRun) second. The only exception is when the user **explicitly** says to skip the safe run (e.g., "just run it on the robot", "skip simulation", "run without debugging only").

The Python simulation catches syntax errors, parse errors, runtime logic errors, and type mismatches **without** any risk of moving hardware. Running HxRun with broken code on a connected robot can cause partial execution, tip crashes, or reagent waste.

### When to Run

Run the debugger whenever you:

- Write or modify a `.hsl` method file
- Need to verify that generated HSL code compiles and executes correctly
- Debug a user-reported runtime error or logic bug
- Want to inspect `Trace()` output to confirm program behavior
- Are asked to test, validate, or execute an HSL file

### Step 1: Start Debugging (Python Simulation -- Safe Run)

**1. Locate the file.** Determine the absolute path of the `.hsl` or `.sub` file. `.med` files cannot be run directly -- they are binary method containers.

**2. Verify it has `method main()`.** If the target is a library without `method main()`, create a test harness (see below).

**3. Run the simulation from the terminal:**

Preferred (bundled exe -- works without Python installed):

```powershell
& "<workspace-root>\HSL Debugger\dist\hsl_debugger\hsl_debugger.exe" "<absolute-path-to-file.hsl>" --hamilton-dir "C:\Program Files (x86)\Hamilton"
```

Fallback (developer mode -- requires Python on PATH):

```powershell
cd "<workspace-root>\HSL Debugger"
python -m hsl_runtime.main "<absolute-path-to-file.hsl>" --hamilton-dir "C:\Program Files (x86)\Hamilton"
```

**Command-line options:**

| Flag | Description |
|------|-------------|
| `--verbose` | Show detailed trace output (default) |
| `--quiet` | Suppress trace output |
| `--dump-tokens` | Print token stream and exit (no execution) |
| `--dump-ast` | Print AST and exit (no execution) |
| `--dump-preprocessed` | Print preprocessed source and exit (no execution) |
| `--max-iterations N` | Loop safety limit (default: 100,000) |
| `--hamilton-dir PATH` | Hamilton installation directory (default: `C:\Program Files (x86)\Hamilton`) |
| `--log-dir PATH` | `.trc` trace log output directory (default: per-user local application data) |

**4. Check the exit code and output.**
- **Exit code 0** = success. Look for `Simulation finished successfully`.
- **Exit code 1** = failure. The terminal output identifies which phase failed.
- **`[HSL TRACE]` lines** show `Trace()` / `FormatTrace()` output.

**5. Fix and re-run if needed.** Repeat until exit code 0. Do **not** proceed to HxRun until the simulation passes.

### Interpreting Python Simulation Results

**Parser warnings are expected.** 50-120 parser warnings for Hamilton library constructs (e.g., `^` operator, struct types) are normal. Focus on errors.

**Preprocessing errors** (phase 1): `#include` file not found or circular include. Check `--hamilton-dir` path.

**Parse errors** (phase 3): HSL syntax errors (missing semicolons, undeclared variables, bad expressions). Most actionable -- fix and re-run.

**Runtime errors** (phase 4): Logic errors (division by zero, array index out of bounds), wrong argument count, missing `method main()`.

**Device calls always succeed in simulation.** Hardware-specific failures only surface in HxRun (Step 2).

### Step 2: Run Without Debugging (HxRun -- Real/Hardware Run)

Only proceed after the Python simulation passes (exit code 0), **or** if the user explicitly requests it.

**Prerequisites:**
- Hamilton VENUS installed; `HxRun.exe` at `C:\Program Files (x86)\Hamilton\Bin\HxRun.exe`
- If a physical instrument is connected, the robot **will execute real movements**

**1. Run via HxRun.exe:**

```powershell
& "C:\Program Files (x86)\Hamilton\Bin\HxRun.exe" "<absolute-path-to-file.hsl>" -t -minimized
```

`-t` = terminate after completion. `-minimized` = minimize HxRun window.

**2. Monitor the trace file:**

```powershell
$methodName = "YourMethodName"
$latest = Get-ChildItem "C:\Program Files (x86)\Hamilton\Logfiles" -Filter "${methodName}_*_Trace.trc" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) { Get-Content $latest.FullName -Tail 50 }
```

**3. Check exit code.** 0 = success. Non-zero = error. Common HxRun-specific failures:
- Labware not found on deck (layout mismatch)
- Tip pickup failures (no tips at expected position)
- Liquid level detection failures (empty wells, foam)
- Hardware communication errors (instrument not connected)
- Sequence position errors (sequence exhausted)

### Validating Code Changes

After modifying HSL code, always:

1. Make the edit
2. Run Python simulation (Step 1)
3. Fix and re-run until exit code 0
4. If the user wants a real run, proceed to HxRun (Step 2)

Do **not** present HSL code as "correct" without running the Python simulation first.

### Creating Test Harnesses for Libraries

Files without `method main()` cannot be executed. Create a temporary test method:

```hsl
// TestHarness.hsl
#include "TargetLibrary.hsl"

method main()
{
   variable result;
   result = TargetNamespace::FunctionToTest("input");
   Trace("Result: ");
   Trace(result);
}
```

Run it through the debugger, then clean up the temporary file.

### Trace Output as Verification

Insert `Trace()` calls at key points to confirm variable values, loop counts, return values, and sequence positions. The `[HSL TRACE]` lines in terminal output (Python simulation) and `.trc` files (HxRun) show values in execution order.
