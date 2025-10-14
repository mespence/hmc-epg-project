#!/bin/bash
#SBATCH --job-name=epg_job          # Name of the job
#SBATCH --output=/data/labs/hopelab/epg/logs/%x_%j.out
#SBATCH --error=/data/labs/hopelab/epg/logs/%x_%j\.err
#SBATCH --gres=gpu:1                # Request 1 GPU
#SBATCH --cpus-per-task=8           # Request 8 CPU cores
#SBATCH --mem=16G                   # Request 16 GB memory
#SBATCH --time=24:00:00             # Max runtime (adjust as needed)
#SBATCH --partition=gpu             # Use a GPU partition if applicable

if ! command -v uv >/dev/null 2>&1; then
  echo "[info] uv not found in PATH; attempting local install to ~/.local/bin"
  # NOTE: Requires outbound internet; if your cluster blocks it,
  # ask your admin to provide a uv module or preinstall it in your image.
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv version: $(uv --version)"

# Create log directory if it doesn’t exist
mkdir -p /data/labs/hopelab/epg/logs

# Print info for debugging
echo "Running on host: $(hostname)"
echo "Job started at: $(date)"
echo "Running job ID: $SLURM_JOB_ID"


# Run your Python script
uv run --extra cu129 model_evaluation.py --data_path /data/labs/hopelab/epg/epg_data/sharpshooter_parquet \
    --save_path /data/labs/hopelab/epg/outputs/sharpshooter_results --model_path unet/unet.py --model_name=unet 

echo "Job finished at: $(date)"
