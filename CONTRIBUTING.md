# Contribute to HILShare

A pull request is welcome on `hilshare/action`, `hilshare/template-rust` and
`hilshare/template-cpp`. Open an issue first for a large change.

## The agreement

Each contributor signs the [contributor licence agreement](CLA.md) one time. A bot asks for the
signature on your first pull request, and the signature takes one click.

You keep the copyright of your contribution. The agreement gives Anmol Kathail a licence to it,
with the right to sublicense it. This right lets HILShare change a licence later with no search for
each earlier contributor. Each contribution stays available under the licence of the repository
at the time of the contribution.

## Before you open a pull request

- In `hilshare/action`, run `python3 -m unittest discover -s test`.
- In a template, build the TAB. A pull request from a fork gets its build, and its board run comes
  after the merge.
- Do not change an input name, an output name or an exit code of the action in a `v1` release.
