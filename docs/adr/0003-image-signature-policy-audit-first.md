# 0003. Enforce image signatures only after Audit proves it

Status: accepted (Audit mode in place)

## Context
CI signs every ECR image with cosign (keyless, GitHub OIDC). Kyverno verifies signatures with an ImageValidatingPolicy. Verification needs registry credentials; on kind these are an ECR login token, which expires after 12 hours. With `failurePolicy: Ignore`, a credential or webhook failure would be silent.

## Decision
Run the policy in Audit mode with `failurePolicy: Ignore` first. Proven: a signed image reports `pass`, an unsigned image reports `fail`. Enforce only once credentials do not depend on a short-lived token (IRSA or Pod Identity on EKS) and failures are visible.

## Consequences
Nothing is blocked yet; unsigned images are only reported. This is deliberate until the credential path is reliable.
