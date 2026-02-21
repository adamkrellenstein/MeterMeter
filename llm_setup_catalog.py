from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class SetupModelProfile:
    key: str
    label: str
    description: str
    ollama_model: str
    gguf_url: str = ""
    gguf_sha256: str = ""
    gguf_filename: str = "model.gguf"


def hardware_options() -> List[dict]:
    return [
        {
            "key": "cpu",
            "label": "CPU (Recommended)",
            "description": "Best compatibility and lower memory pressure.",
        },
        {
            "key": "gpu",
            "label": "GPU",
            "description": "Higher throughput if your local runtime supports acceleration.",
        },
    ]


def model_profiles_for_hardware(hardware_key: str) -> List[SetupModelProfile]:
    hardware = (hardware_key or "cpu").strip().lower()

    if hardware == "gpu":
        return [
            SetupModelProfile(
                key="small",
                label="Small/Fast (Recommended)",
                description="Quick responses, lower quality ceiling.",
                ollama_model="qwen2.5:3b-instruct",
                gguf_filename="Qwen2.5-3B-Instruct-Q4_K_M.gguf",
            ),
            SetupModelProfile(
                key="better",
                label="Better Accuracy",
                description="Higher quality meter hints with larger model footprint.",
                ollama_model="qwen2.5:7b-instruct",
                gguf_filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
            ),
        ]

    return [
        SetupModelProfile(
            key="small",
            label="Small/Fast (Recommended)",
            description="Optimized for broad laptop compatibility.",
            ollama_model="qwen2.5:3b-instruct",
            gguf_filename="Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        ),
        SetupModelProfile(
            key="better",
            label="Better Accuracy",
            description="More accurate analysis at higher RAM/latency cost.",
            ollama_model="qwen2.5:7b-instruct",
            gguf_filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        ),
    ]
