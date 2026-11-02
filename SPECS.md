# The HILShare contract

Version 8. This document specifies the app file that HILShare accepts, the lines that a board
prints, and the result that HILShare gives for them.

Two sources print on one console. The **kernel** is the Tock kernel that HILShare supplies.
The kernel reports facts: that it booted, and how the process of the app ended. The **app**
is the code under test. The app reports the verdict. HILShare copies that verdict to the exit
code. HILShare does not calculate a verdict from numbers, so this document gives no definition
of a check.

## The app file

HILShare takes a TAB. A TAB is a tar archive that elf2tab writes. It holds one TBF for each
architecture of the app. HILShare installs the TBF of the architecture of the board, for
example `cortex-m4.tbf`.

Before HILShare looks for the board, it checks the TBF against the board definition. If a
check fails, the run ends at exit code 2 with a sentence that names the correction, and
HILShare does not install the app. `hilshare preflight --board <board> <app.tab>` does the same
checks with no board.

| Check | Value on the Nucleo-F411RE |
|---|---|
| The TAB holds `<architecture>.tbf` | `cortex-m4.tbf` |
| The TBF header is version 2 | |
| The header size is 16 bytes or more, and not more than the TBF | |
| The header checksum is correct | |
| `total_size` is the length of the TBF | |
| Each header entry fits in the header, and a known entry has its correct length | |
| `total_size` fits in the app flash | 262144 bytes |
| The `enabled` flag is set | |
| The header has a Main entry or a Program entry | |
| A kernel version entry has the major version of the kernel, and a minor version that is not above that of the kernel | Tock 2.3 |
| A fixed flash address is the app flash start plus the header size plus the protected trailer | `0x08040000` plus those sizes |
| A fixed RAM address is inside the app RAM | `0x20004000` to `0x20020000` |
| `minimum_ram_size` fits in the process block at the RAM address of the app | Refer to "The RAM of an app" |

The Program entry, if present, gives `minimum_ram_size` and the protected trailer. If it is
not present, the Main entry gives them. A TBF with no kernel version entry or no fixed
addresses entry is not checked for that entry. Apps from libtock-rs have the fixed addresses
entry. Cortex-M apps from libtock-c are position-independent and do not. RISC-V apps from
libtock-c have fixed addresses.

The Tock loader does not start a TBF that fails one of these checks. Without the checks, such
a run uses an install and ends at exit code 5.

### The RAM of an app

The kernel gives a process one block of RAM. The memory protection unit of the board sets the
sizes and the addresses of a block. On the Nucleo-F411RE, a block is a power of two and starts
at a multiple of half its size. The kernel takes 1128 bytes of the block when it creates the
process, and `minimum_ram_size` must fit in the remainder.

| RAM address of the app | Largest block | Largest `minimum_ram_size` |
|---|---|---|
| `0x20004000` | 32 KB | 31640 bytes |
| `0x20008000` | 64 KB | 64408 bytes |
| `0x20010000` | 64 KB | 64408 bytes |
| No fixed address | 64 KB | 64408 bytes |

HILShare accepts each other address that can start a block that holds the app.

## The structure of a run

HILShare resets the board and reads the console to the boot line. If the boot line has the
correct version, HILShare installs the app and resets the board again. It then reads the
console from that reset. HILShare keeps no text of the first read, because the app that ran
before the install printed it. `␞` is the byte `0x1E`.

```
␞HILSHARE_KERNEL_BOOT version=0.2.0 clock_hz=100000000
HILSHARE_START_EXECUTION
HILSHARE_CHECK name=arithmetic status=PASS
HILSHARE_RESULT=PASS
␞HILSHARE_KERNEL_APP_EXIT name=my-app code=0 flash=8192 ram=8192 stack=512 heap=0 grants=1024 syscalls=12 timeslices=0
```

- HILShare reads nothing before the boot line.
- HILShare reads app lines from the boot line to `HILSHARE_RESULT`. It reads nothing that the
  app prints after its result.
- The run ends at `HILSHARE_KERNEL_APP_EXIT`, `HILSHARE_KERNEL_APP_FAULT`,
  `HILSHARE_KERNEL_APP_NOT_LOADED` or `HILSHARE_KERNEL_PANIC`. HILShare reads nothing after
  that line.

## Line cleanup

A line is the text between two newlines. Before HILShare examines a line, it does this
cleanup:

- It removes each escape sequence, such as a colour code:
  - `ESC [` and the bytes after it, through the first byte in `0x40`–`0x7E`
  - `ESC ]` and the bytes after it, through `BEL` or `ESC \`
  - any other `ESC` and the one byte after it

  A sequence that does not end takes the remainder of the line.
- It replaces each byte that is not valid UTF-8 with `U+FFFD`.
- It removes all text before the first printable ASCII character (`0x20`–`0x7E`).
- It removes the spaces and the tabs from the two ends.

After the cleanup, the line is an app marker if it starts with `HILSHARE_`. If it does not,
HILShare ignores the line. An app can print any other text.

## Kernel markers

The kernel prints five lines. Each has the byte `0x1E` directly before `HILSHARE_KERNEL_`:

| Line | When |
|---|---|
| `␞HILSHARE_KERNEL_BOOT version=<v> clock_hz=<hz>` | The kernel booted. This line is mandatory. `<v>` must be the `kernel` value in the board definition. `<hz>` is the CPU clock rate in hertz |
| `␞HILSHARE_KERNEL_APP_EXIT name=<name> code=<code> <statistics>` | The app exited. `code` is the completion code of the app. `code` is absent when the app ended without a completion code |
| `␞HILSHARE_KERNEL_APP_FAULT name=<name> reason=<reason> addr=<a> pc=<p> <statistics>` | The app faulted, and the kernel stopped it |
| `␞HILSHARE_KERNEL_APP_NOT_LOADED reason=<reason>` | The loader did not load the app, so no process ran |
| `␞HILSHARE_KERNEL_PANIC file=<file> line=<line>` | The kernel panicked and stopped. `file` and `line` give the source location of the panic. They are absent when the kernel does not know the location |

The kernel changes each `0x1E` that an app writes to `?`. Thus, only the kernel can put that
byte before `HILSHARE_KERNEL_`. HILShare finds a kernel marker at any position in a line. When
a fault interrupts a line of the app, the kernel marker comes at the end of that line.
HILShare discards all text before the `0x1E`. The line cleanup starts after the `0x1E`.

A line can hold two `0x1E` bytes before `HILSHARE_KERNEL_`. This occurs when a reset stops the
kernel in the middle of a kernel line, before its newline. The next line that the kernel
prints then continues that line. HILShare uses the last `0x1E` of the line, and discards all
text before it.

The prefix `HILSHARE_KERNEL_` belongs to the kernel. An app line that starts with that
prefix, with no `0x1E` before it, is an error.

Each field has the form `key=value`. HILShare splits the fields as it splits a check line.
In `name`, the kernel replaces each character of the app name outside `!`–`~` with `_`.
`addr` and `pc` are `0x` and eight hexadecimal digits.

Each `<statistics>` value is in bytes, unless the field is a count:

| Field | Description |
|---|---|
| `flash` | The flash region of the app, with its header |
| `ram` | The RAM that the kernel allocated to the app |
| `stack` | The deepest stack that the kernel saw. The kernel samples the stack pointer at system calls. Thus, the value is a floor of the peak stack use, not a high-water mark. Absent when the kernel does not know it |
| `heap` | The distance from the heap start to the app break. Absent when the kernel does not know it |
| `grants` | The kernel memory that the grants of the app used |
| `syscalls` | The number of system calls |
| `timeslices` | The number of times that the app used its full timeslice |

On the Nucleo-F411RE, the `reason` of `HILSHARE_KERNEL_APP_FAULT` is one of these values. A
board with another memory protection unit adds its own values.

- `mpu_data`
- `mpu_instruction`
- `mpu_stacking`
- `bus`
- `usage`
- `hard`

`addr` is the data address of the fault. It is present when the core latched an address.
`pc` is the instruction address of the fault. It is present when the stack of the app held
a readable exception frame.

The `reason` of `HILSHARE_KERNEL_APP_NOT_LOADED` is one of these values:

| `reason` | Description |
|---|---|
| `not_enough_memory` | No block of the app RAM holds the app |
| `memory_address` | The block of the app does not start at its fixed RAM address |
| `mpu_flash_length` | The memory protection unit cannot cover the length of the TBF |
| `mpu_configuration` | The memory protection unit refused the configuration of the app |
| `no_process_slot` | The kernel has no process slot for the app |
| `binary` | The loader refused the TBF |
| `check` | The credential check refused the TBF |
| `internal` | The loader reports an internal error of the kernel |

After the panic line, the kernel prints the Tock panic dump. The dump can repeat lines
that the kernel printed before. HILShare reads nothing after the panic line.

HILShare reads `version` to make sure that the kernel is correct, and it quotes `reason`,
`file` and `line`.
HILShare copies all other fields to the reports and compares them with nothing. `code` is not
the verdict.

HILShare does not read `clock_hz`. The clock driver gives the same rate to the app.

## The clock driver

The kernel has a system call driver that gives the CPU clock rate to the app. An app
converts the cycles of the `CycleCount` driver (`0x90008`) to time with this rate.

| Driver number `0xA0000` | Return |
|---|---|
| Command 0 | Success. The driver exists |
| Command 1 | Success with one `u32`: the CPU clock rate in hertz, the same as `clock_hz` |
| Other commands | `NOSUPPORT` |

## The alarm driver

The kernel has the Tock alarm driver, driver number `0x0`. The tick rate is a fact of the
board, so an app reads it from command 1.

| Command | Return |
|---|---|
| 1 | Success with one `u32`: the tick rate in hertz |
| 2 | Success with one `u32`: the tick count. It wraps after 2³² ticks |

The other commands of the driver are the commands of the Tock alarm driver. An app sleeps
through them.

On the Nucleo-F411RE, TIM2 counts the ticks at 16000 hertz, so the count wraps after
approximately 74 hours. A probe reset halts the core for approximately 24 ms. TIM2 counts
through the halt and the cycle counter does not, so an app that compares the two must wait
100 ms after the reset.

## App markers

Two markers are mandatory.

| Marker | Description |
|---|---|
| `HILSHARE_START_EXECUTION` | The run started. This marker has no value |
| `HILSHARE_RESULT=PASS` or `HILSHARE_RESULT=FAIL` | The verdict |

The start marker lets HILShare tell an app that did not run from an app that ran and stopped
before its result.

The other markers are optional.

| Marker | Description |
|---|---|
| `HILSHARE_FIRMWARE=<value>` | The version of the app, for example `0.1.0` |
| `HILSHARE_CHECKS_PASSED=<value>` | The tally of the app, for example `6/6`. HILShare copies it and does not examine it |
| `HILSHARE_CHECK …` | The result of one check. Refer to "Check lines" |

HILShare copies any other `HILSHARE_<KEY>=<value>` line without change. Thus, an app can print
its own data, and this document does not change.

## Check lines

```
HILSHARE_CHECK name=vector_dot status=PASS cycles=1319 value=2e1
```

To parse a check line, split the text after `HILSHARE_CHECK` at the spaces. Then split each
part at its first `=`.

- `name` and `status` are mandatory.
- `status` is `PASS`, `FAIL` or `SKIP`.
- All other keys belong to the app. HILShare copies them and reads none of them.
- A value cannot contain a space. There are no quotation marks. Use an underscore.
- A key that occurs two times on one line is an error.
- The same `name` on two lines is not an error. HILShare keeps the two lines in sequence.

These optional keys have a fixed meaning. An app that reports time or stack use uses
them:

| Key | Meaning |
|---|---|
| `cycles` | The cycles of the timed section, net of the round trip of the cycle counter |
| `us` | `cycles × 1 000 000 / clock_hz`, in microseconds, as a decimal number |
| `overhead_us` | The round trip of the cycle counter that the app subtracted, in microseconds |
| `budget_us` | The time limit that the app set for the check, in whole microseconds |
| `stack_bytes` | The peak stack depth during the check, in bytes, from the stack top |

A read of the cycle counter is a system call. Two reads with no work between them give
its round trip. HILShare copies these keys and reads none of them. The app sets `status`.

Do not time a section that sleeps with `cycles`. The CPU sleeps in `WFI`, and
`DBGMCU_CR.DBG_SLEEP` controls whether `CYCCNT` counts in `WFI`. The debug probe sets that
bit, and the kernel does not control it. Time a section that sleeps with the ticks of the
alarm driver.

`SKIP` shows that the app did not run that check. It is not a failure. The verdict comes
from `HILSHARE_RESULT` only. HILShare does not count the check statuses to get the verdict.

## How a run ends

A run whose app file fails a check of "The app file" ends at exit code 2 before HILShare
looks for the board. For all other runs, HILShare tests these conditions in this sequence.

| Contents of the capture | Exit code | New attempt |
|---|---|---|
| No boot line before the boot time limit of the board, and the kernel memory, read through the probe, holds a boot line with the version of the board | 3 | yes |
| No boot line before the boot time limit of the board in all other cases, a boot line with no `version`, or a boot line with a different version | 10 | no |
| A panic line, with any app text before it | 11 | no |
| A not-loaded line | 5 | no |
| A fault line, with any app text before it | 9 | no |
| A line that this document does not describe (refer to the last section) | 7 | no |
| An exit line and no start marker | 5 | no |
| An exit line, a start marker and no result | 6 | no |
| An exit line and a result | 0 for `PASS`, 1 for `FAIL` | no |
| No exit line before the time limit, and no start marker | 5 | yes |
| No exit line before the time limit, a start marker and no result | 6 | no |
| No exit line before the time limit, after a result | 6 | no |

HILShare reads the kernel memory only if two conditions are true: the board definition has
`[transport.kernel_memory]`, and the console carried no boot line. A boot line in the
memory shows that the kernel booted and that the console did not deliver the line. HILShare
reads its `version` and nothing else.

HILShare also applies the first two rows of the table to the read before the install. If one
of them applies, HILShare does not install the app.

An app that prints its result and does not exit did not complete the run. A run ends when
the kernel reports that the process ended.

A panic before the boot line is not read, because HILShare reads nothing before the boot
line. Such a run ends at code 10.

## Lines that this document does not describe

Each of these lines fails the run. HILShare does not ignore them.

| Event | Cause |
|---|---|
| An app marker before the start marker | The app printed results before it reported the start |
| A second start marker | The app started two times in one boot |
| A second boot line | The kernel started again in the middle of the run. The capture holds two runs |
| A marker with no `=`, a check line that HILShare cannot read, or a result that is not `PASS` or `FAIL` | The app printed an incorrect marker |
| An app line that starts with `HILSHARE_KERNEL_` | That prefix belongs to the kernel |
| A kernel line that is not one of the five kernel markers | This HILShare version does not support that kernel |
