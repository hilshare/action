# HILShare

HILShare runs a Tock app on a physical board and reports the result as a check on your workflow.
You need no board, no token and no secret. Your workflow builds a TAB, the app file of Tock, on a
hosted runner. This action sends the TAB to the HILShare service, and a host installs it on the
board. The action then prints what the board printed, and exits with the exit code of the run.

The app decides pass or fail. [SPECS.md](SPECS.md) gives the lines that an app must print. The
[Rust template](https://github.com/hilshare/template-rust) and the
[C++ template](https://github.com/hilshare/template-cpp) are the fastest way to start.

The board today is an ST Nucleo-F411RE: a Cortex-M4F core at 100 MHz, with a HILShare Tock kernel.
Each run is an unprivileged Tock process. The
[status page](https://hilshare.anmolkathail.workers.dev) shows the board and the queue.

A run tests code that computes. The kernel gives an app a console, a cycle counter, the clock rate
and an alarm. An app cannot use a peripheral, an interrupt or a register of the core, so a driver, an
RTOS or a HAL does not run here.

## Use

```yaml
permissions:
  contents: read
  id-token: write

steps:
  - uses: hilshare/action@v1
    with:
      app: target/tab/my-app.tab
      board: nucleo-f411re
```

The job needs `id-token: write`. The service identifies your account with the OIDC token of the job.
If your organization limits actions, it must allow `hilshare/action`.

A pull request from a fork or from Dependabot gets no OIDC token. Add this condition to the step, so
the check stays green, and the app gets its board run after the merge:

```yaml
if: ${{ github.event_name != 'pull_request' || (github.event.pull_request.head.repo.full_name == github.repository && github.event.pull_request.user.login != 'dependabot[bot]') }}
```

## A required check

A required check goes red when the board is offline, and that stops each merge. Exit codes 20, 21,
23, 24, 25, 26 and 29 do not come from your app. To let them pass, use these two steps:

```yaml
- name: Run on the board
  id: board
  continue-on-error: true
  uses: hilshare/action@v1
  with:
    app: target/tab/my-app.tab
    board: nucleo-f411re
- name: Judge the run
  shell: bash
  env:
    EXIT_CODE: ${{ steps.board.outputs.exit-code }}
  run: |
    case "$EXIT_CODE" in
      20|21|23|24|25|26|29) echo "::warning::no board result, exit $EXIT_CODE" ;;
      *) exit "$EXIT_CODE" ;;
    esac
```

## Inputs

| Input | Required | Description |
|---|---|---|
| `app` | yes | The path of the TAB |
| `board` | yes | The board, for example `nucleo-f411re` |
| `expected-exit` | no | The exit code that the run must give, from 0 to 11. The default is 0. Set it to 1 or 9 for an app that must fail or fault |

## Outputs

| Output | Description |
|---|---|
| `exit-code` | The exit code of the run, or the exit code of the action |
| `job-id` | The ID of the job. It is empty if the service accepted no job |

The step summary shows the checks and the kernel statistics. The artifact
`hilshare-<TAB name>-<board>` holds the log of the board and the JUnit file for 7 days.

## Exit codes

If the run gives `expected-exit`, the step exits 0. In all other cases, the step exits with one of
these codes:

| Code | Description |
|---|---|
| 1 | The app reported `HILSHARE_RESULT=FAIL` |
| 2 | The TAB fails preflight. An app that asks for too much RAM gives this code. The sentence names the correction |
| 3 | The console closed, the host did not find the board, or the console gave no boot line |
| 4 | The install command, the reset command or the kernel memory command failed |
| 5 | The app did not start a run, or the kernel did not load it. The sentence gives the reason of the kernel |
| 6 | The app started a run and did not complete it. A panic, an `assert` and a hang give this code |
| 7 | The board printed a line that [SPECS.md](SPECS.md) does not describe |
| 8 | All attempts failed without a verdict |
| 9 | The app faulted. A stack overflow also gives this code |
| 10 | The board is not provisioned, or its kernel version is incorrect |
| 11 | The kernel panicked |
| 20 | The queue stayed full for 10 minutes. Run the job again later |
| 21 | The board stayed offline or missing for 10 minutes, or it is paused. The status page shows it |
| 22 | The service refused the job. The sentence gives the reason |
| 23 | The host lost the job. Run the job again |
| 24 | The job did not reach the board in 60 minutes |
| 25 | The job ended with no result: it expired, or it was cancelled |
| 26 | The service did not answer |
| 27 | The workflow is incorrect: no `id-token: write`, no TAB, an empty `board`, an incorrect `expected-exit`, or no `python3` on the runner |
| 28 | The run gave 0, and `expected-exit` is not 0 |
| 29 | The owner used its 20 runs of the day |

## Limits

| Limit | Value |
|---|---|
| TAB | 1 MiB |
| A run | 10 s from the boot line to the exit line, with 3 attempts for an app that prints nothing |
| Log, JUnit file, summary | 1 MiB each. Above this limit, the service keeps the first part of the file, and a line in the file says so. The exit code does not change |
| Jobs of one owner | 1 in the queue or on the board. The action waits and submits again |
| Runs of one owner | 20 in a day, from 00:00 UTC, for all repositories of the owner. A job that does not start uses no run |
| Age of the owner account | 30 days |
| The wait of the action | 60 minutes in the queue, and 10 minutes for an outage |

The job of your workflow waits while its job is in the queue. A private repository pays for those
minutes.

The service can run one job at a time on each board. A result is advisory. Do not use it as the only
check on code that can cause harm. The operator can read your app while the job runs, so do not submit
an app that you cannot show to the operator.

## Stability

The contract in [SPECS.md](SPECS.md) grows by new keys, new lines and new fault reasons only. An app
that follows the contract today continues to work. A `v1` release keeps each input, each output and
each exit code. Each kernel version change gets a release note here first, because a change of the
kernel can turn a budget red with no commit of yours.

## Terms

Each submission accepts the [terms](TERMS.md). They say what the service stores.

## Contribute

Each contribution needs a signature on the contributor licence agreement.
[CONTRIBUTING.md](CONTRIBUTING.md) says why.

## Licence

This repository is open source under the [Apache License 2.0](LICENSE). The template repositories
are also under that licence. The licence covers these repositories, and not the HILShare service,
the harness or the kernel.

HILShare is a trademark of Anmol Kathail. The licence gives no right to the name. A fork must use a
different name.
