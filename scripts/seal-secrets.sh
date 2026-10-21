#!/usr/bin/env bash
# Creates the two sealed values for the Helm chart and prints them as a values snippet.
#
#   ./scripts/seal-secrets.sh <namespace> [secret-name] [controller-namespace] [controller-name]
#
# Asks for the first sign-in password. Generates a random APP_SECRET_KEY.
# Needs oc (or kubectl), kubeseal and openssl, and access to the sealed-secrets controller's certificate.
set -euo pipefail

NS="${1:?Usage: $0 <namespace> [secret-name] [controller-namespace] [controller-name]}"
NAME="${2:-parser-eval-secrets}"
CTRL_NS="${3:-kube-system}"
CTRL_NAME="${4:-sealed-secrets-controller}"

read -r -s -p "Password for the first sign-in (at least 8 characters): " PASSWORD
echo
if [ "${#PASSWORD}" -lt 8 ]; then echo "Use at least 8 characters." >&2; exit 1; fi
KEY="$(openssl rand -base64 32)"

seal() {
  printf '%s' "$2" | kubeseal --raw --namespace "$NS" --name "$NAME" --from-file=/dev/stdin \
    --controller-namespace "$CTRL_NS" --controller-name "$CTRL_NAME"
}

cat <<EOF
# Paste into your values file (for example values-prod.yaml):
secrets:
  name: ${NAME}
  sealedSecret:
    enabled: true
    encryptedData:
      APP_SECRET_KEY: $(seal APP_SECRET_KEY "$KEY")
      APP_INITIAL_PASSWORD: $(seal APP_INITIAL_PASSWORD "$PASSWORD")
EOF
