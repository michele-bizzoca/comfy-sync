#!/bin/bash

nvidia-smi | sed -nE 's/.*CUDA Version: ([0-9]+)\.([0-9]+).*/\1.\2/p'
