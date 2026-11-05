# 6. The agent can only propose a revert of a gitops commit

## Status
Accepted

## Context
Finding a bad release is only useful if someone can act on it quickly, but an agent that changes production is dangerous. It reads logs and commit messages, which other people can influence, and its diagnosis can be wrong. The LLM evaluation in `docs/RESULTS.md` showed 9 of 33 fault reports were wrong or misleading.

## Decision
The agent has exactly one action: open a pull request that reverts one commit.

- **Eligible commits:** a single-parent commit from the last 30 minutes, with every file under `gitops/`, including the old path of a renamed file. Reverts are excluded, so the agent can't undo its own proposal.
- **When:** only after `canary_aborted`, `canary_errors` or `errors_after_release`, and only if that commit is the only non-merge commit under `gitops/` in the window. With two or more, no pull request is opened, because the choice belongs to a person.
- **Conflicts:** if a file has changed since the commit, nothing is proposed, because the revert could undo newer work.
- **The rules choose, not the LLM.** The original plan let the LLM pick from a list built by the backend. The model didn't improve on the rules and is off by default, so this removes log text and model output from the decision altogether.
- **Text:** the title and commit message are fixed. The commit subject appears only inside a fenced block marked as untrusted.
- **Identity:** a GitHub App with Contents and Pull requests write access, and no Workflows permission, because a bot that could add a workflow could read the deploy key. The App has no bypass on the `protect-main` ruleset, so it can't push to `main` or merge without approval, and its author can't approve its own pull request. Each token lasts an hour and is limited to this repo.
- **Kubernetes:** the agent's RBAC stays read-only. Changes reach the cluster only through a merge, then Argo CD.
- **Off by default:** with no App key, the agent never contacts GitHub with write access.

## Consequences
- A person still has to notice, review and merge, so this doesn't shorten recovery by itself. A canary abort already protects users; the revert brings Git back in line.
- Merging an app change creates two deploy commits, which makes the choice ambiguous for 30 minutes, so no pull request is opened in that time.
- A revert can be wrong: the commit may only be close in time to the fault. The pull request says this is a correlation.
- A closed pull request leaves its branch behind, which is what stops the same revert being proposed again. Deleting that branch allows it.
- GitHub's Contents write permission also allows pushing to other branches. Only the ruleset protects `main`, so it must stay active.
- Faults with no eligible commit, such as a dependency outage, get no proposal.

## Alternatives considered
- **Auto-merge or direct rollback:** rejected. A wrong diagnosis would then change production with nobody checking it.
- **Letting the agent write fixes:** rejected. A revert returns to a state that already ran, while new changes have never been tested.
- **A personal access token:** rejected. It would carry the owner's bypass of the ruleset, so the bot could push to `main`.
- **`git` in the container:** not needed. GitHub's API can build the commit, and the image stays smaller with a read-only filesystem.
