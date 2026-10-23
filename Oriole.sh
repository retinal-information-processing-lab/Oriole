#!/bin/bash
# Launch the Oriole GUI in its conda environment (creates the env on first use).
# The GUI is started detached, so the terminal that ran this script can close immediately.
# Output of the GUI goes to ~/.oriole/oriole.log.
parent_path=$(cd "$(dirname "${BASH_SOURCE[0]}")" ; pwd -P)
cd "$parent_path"

if [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then
    source "/opt/conda/etc/profile.d/conda.sh"
fi

if ! conda env list | grep -qE "^oriole[[:space:]]"; then
    echo "Environment 'oriole' not found: creating it from environment.yml (a few minutes)..."
    conda env create -f environment.yml -y || { echo "Environment creation failed."; read -p "Press Enter to close."; exit 1; }
fi

conda activate oriole
mkdir -p "$HOME/.oriole"
setsid python -m oriole gui "$@" > "$HOME/.oriole/oriole.log" 2>&1 < /dev/null &
disown
