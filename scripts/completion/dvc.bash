#!/usr/bin/env bash

#----------------------------------------------------------
# Repository:  https://github.com/iterative/dvc
#
# References:
#   - https://www.gnu.org/software/bash/manual/html_node/Programmable-Completion.html
#   - https://opensource.com/article/18/3/creating-bash-completion-script
#----------------------------------------------------------

_dvc_commands='init destroy add import checkout run pull push fetch \
              status repro remove move gc config remote metrics install \
              root lock unlock pipeline'

_dvc_global_options="-h --help -q --quiet -V --verbose"
_dvc_options="-h --help -v --version"
_dvc_init="--no-scm -f --force"
_dvc_destroy="-f --force"
_dvc_add="-R --recursive"
_dvc_run="--no-exec -f --file -c --cwd -d --deps -o --outs -O --outs-no-cache -M --metrics-no-cache -y --yes"
_dvc_pull="--show-checksums -j --jobs -r --remote -a --all-branches -T --all-tags -d --with-deps"
_dvc_push="--show-checksums -j --jobs -r --remote -a --all-branches -T --all-tags -d --with-deps"
_dvc_fetch="--show-checksums -j --jobs -r --remote -a --all-branches -T --all-tags -d --with-deps"
_dvc_status="--show-checksums -j --jobs -r --remote -a --all-branches -T --all-tags -d --with-deps -c --cloud"
_dvc_repro="--dry -f --force -s --single-item -c --cwd -m --metrics -i --interactive -p --pipeline -P --all-pipelines"
_dvc_remove="--dry -o --outs -p --purge"
_dvc_gc="-a --all-branches -T --all-tags -c --cloud -r --remote"
_dvc_config="-u --unset --local"
_dvc_checkout="$(compgen -G '*.dvc')"
_dvc_move="$(compgen -G '*')"
_dvc_lock="$(compgen -G '*.dvc')"
_dvc_unlock="$(compgen -G '*.dvc')"
_dvc_import=""
_dvc_remote=""
_dvc_metrics=""
_dvc_install=""
_dvc_root=""
_dvc_pipeline=""

# Notes:
#
# `COMPREPLY` contains what will be rendered after completion is triggered
#
# `word` refers to the current typed word
#
# `${!var}` is to evaluate the content of `var` and expand its content as a variable
#
#       hello="world"
#       x="hello"
#       ${!x} ->  ${hello} ->  "world"
#
_dvc () {
  local word="${COMP_WORDS[COMP_CWORD]}"

  COMPREPLY=()

  if [ "${COMP_CWORD}" -eq 1 ]; then
    case "$word" in
      -*) COMPREPLY=($(compgen -W "$_dvc_options" -- "$word")) ;;
      *)  COMPREPLY=($(compgen -W "$_dvc_commands" -- "$word")) ;;
    esac
  elif [ "${COMP_CWORD}" -eq 2 ]; then
    local options_list="_dvc_${COMP_WORDS[1]}"

    COMPREPLY=($(compgen -W "$_dvc_global_options ${!options_list}" -- "$word"))
  fi

  return 0
}

complete -F _dvc dvc
