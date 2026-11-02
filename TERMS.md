# The terms of the HILShare service

These terms apply to the HILShare service at https://hilshare.anmolkathail.workers.dev. Anmol
Kathail operates the service. A submission of a job accepts these terms.

## The service

- The service is free, and it has no warranty. It runs on one board on one host, and it can stop at
  any time.
- A result is advisory. Do not use a result as the only check on code that can cause harm.
- The operator can refuse any job and any account, with no notice.
- The operator is not liable for a loss that comes from the service or from a result.

## Your app

- You must have the right to run the app that you submit.
- Do not submit an app that attacks the board, the kernel, the host or the service.
- Do not print a secret from your app. The log goes to your workflow run and stays in the service
  for 7 days.
- The host installs your app on the board, so the operator can read it while the job runs.

## What the service stores

| Data | Time |
|---|---|
| The TAB | Until one minute after the job ends |
| The owner ID, the repository name, the board, the times, the exit code and the install count of a job | 7 days from the end of the job |
| The log, the JUnit file and the step summary of a run | 7 days from the end of the job |
| The owner ID and the creation time of the owner account, from the GitHub users API | With no time limit |
| The ID of each OIDC token | Until the token expires |

The status page is public. It shows the first 8 characters of each job ID, the host, the board, the
times and the exit code. It shows no repository name and no owner.

Cloudflare carries each request and holds the data of the service. GitHub issues the OIDC token and
the account data.

## Contact

Open an issue on [hilshare/action](https://github.com/hilshare/action/issues). To remove your data
before its time, open an issue with your owner ID. Do not put a secret in an issue.
