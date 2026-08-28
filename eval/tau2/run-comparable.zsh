#!/usr/bin/env zsh
# Ablation harness, official base splits, 4 trials.
# Sierra-comparable user simulator (needs a real OPENAI_API_KEY):
#   zsh eval/tau2/run-comparable.zsh
#   zsh eval/tau2/run-comparable.zsh airline
# DeepSeek-only (user simulator + dsh agent both use DEEPSEEK_API_KEY):
#   TAU2_USER_LLM=deepseek/deepseek-v4-flash zsh eval/tau2/run-comparable.zsh airline
# Resume one domain with the same --save-to (auto_resume).
# DeepSeek-user runs write *-t4-dsuser so they never mix with gpt-4.1 checkpoints.
set -euo pipefail

HERE="${0:A:h}"
REPO="${HERE:h:h}"
TAU2_ROOT="${TAU2_ROOT:-$HOME/Desktop/projects/tau2-bench}"
SIERRA_USER_LLM="gpt-4.1-2025-04-14"
USER_LLM="${TAU2_USER_LLM:-$SIERRA_USER_LLM}"
AGENT_MODEL="${DSH_MODEL:-deepseek-v4-flash}"
SEED="${TAU2_SEED:-300}"
NUM_TRIALS=4
LAYER=ablation
SPLIT=base

# LiteLLM routes unprefixed names (including DSH_MODEL) to OpenAI. A DeepSeek
# user simulator must be deepseek/<model>.
case "$USER_LLM" in
  deepseek/*) ;;
  deepseek-*) USER_LLM="deepseek/$USER_LLM" ;;
esac

_import_env_file() {
  local file="$1" line key val
  [[ -r "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == export[[:space:]]* ]] && line="${line#export }"
    key="${line%%=*}"
    val="${line#*=}"
    [[ "$key" == [A-Za-z_][A-Za-z0-9_]* ]] || continue
    if [[ -z "${(P)key:-}" ]]; then
      if [[ "$val" == \'*\' || "$val" == \"*\" ]]; then
        val="${val[2,-2]}"
      fi
      export "$key=$val"
    fi
  done < "$file"
}

_is_placeholder_key() {
  local v="${1:-}"
  [[ -z "$v" || "$v" == \<your_key_here\> || "$v" == your_key_here ]]
}

_user_llm_needs_openai() {
  case "$1" in
    deepseek/*) return 1 ;;
    gpt-*|openai/*|o1-*|o3-*|o4-*) return 0 ;;
    *) return 0 ;;
  esac
}

if [[ ! -f "$REPO/pnpm-workspace.yaml" ]]; then
  print -u2 "expected DeepSeek Harness repo at $REPO"
  exit 1
fi
if [[ ! -d "$TAU2_ROOT/src/tau2" ]]; then
  print -u2 "tau2 checkout not found at $TAU2_ROOT (set TAU2_ROOT)"
  exit 1
fi

_import_env_file "$REPO/.env"
_import_env_file "$TAU2_ROOT/.env"

if _is_placeholder_key "${OPENAI_API_KEY:-}"; then
  unset OPENAI_API_KEY
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  print -u2 "set DEEPSEEK_API_KEY (dsh agent). Export it or put it in $REPO/.env"
  exit 1
fi
if _user_llm_needs_openai "$USER_LLM"; then
  if [[ -z "${OPENAI_API_KEY:-}" || "${OPENAI_API_KEY}" == "${DEEPSEEK_API_KEY}" ]]; then
    print -u2 "user simulator $USER_LLM talks to OpenAI. DEEPSEEK_API_KEY is not an OpenAI key."
    print -u2 "DeepSeek-only (same key for user + agent):"
    print -u2 "  TAU2_USER_LLM=deepseek/deepseek-v4-flash zsh $0 ${*:-airline}"
    print -u2 "Sierra-comparable Pass^k still needs a real OPENAI_API_KEY for $SIERRA_USER_LLM."
    exit 1
  fi
fi

domains=("$@")
if (( ${#domains} == 0 )); then
  domains=(airline retail telecom)
fi

export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$REPO/tmp/py-sdk-venv}"
export TAU2_DATA_DIR="${TAU2_DATA_DIR:-$TAU2_ROOT/data}"
cd "$REPO"

if [[ "$USER_LLM" == "$SIERRA_USER_LLM" ]]; then
  SAVE_SUFFIX=t4
else
  SAVE_SUFFIX=t4-dsuser
fi

print "repo=$REPO"
print "tau2_root=$TAU2_ROOT"
print "tau2_data_dir=$TAU2_DATA_DIR"
print "layer=$LAYER split=$SPLIT num_trials=$NUM_TRIALS seed=$SEED"
print "user_llm=$USER_LLM agent_model=$AGENT_MODEL save_suffix=$SAVE_SUFFIX"
print "domains=${domains[*]}"
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  openai_key_state=set
else
  openai_key_state=unset
fi
print "openai_api_key=$openai_key_state openai_api_base=${OPENAI_API_BASE:-${OPENAI_BASE_URL:-unset}}"

for domain in "${domains[@]}"; do
  save_to="dsh-ablation-${domain}-base-${SAVE_SUFFIX}"
  print -- "--- $domain save_to=$save_to ---"
  uv run --project python/sdk python eval/tau2/run.py \
    --layer "$LAYER" \
    --domain "$domain" \
    --split "$SPLIT" \
    --num-trials "$NUM_TRIALS" \
    --seed "$SEED" \
    --user-llm "$USER_LLM" \
    --model "$AGENT_MODEL" \
    --timeout 900 \
    --max-steps 200 \
    --save-to "$save_to"
  print "tau2_simulations=$TAU2_DATA_DIR/simulations/$save_to/results.json"
done
