#!/bin/bash
srun --cpus-per-task=16 --gres=gpu:1 --mem 16000 --time 5-00:00 --job-name vipaint --partition=gpu --pty bash
