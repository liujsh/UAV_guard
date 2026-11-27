"""Hash-verified NumPy inference for the frozen TD3 residual actor."""

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray


POLICY_DIRECTORY = Path(__file__).resolve().parent / "policies"


def _read_verified(path: Path, expected_sha256: str) -> bytes:
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(f"策略资产哈希校验失败：{path.name}")
    return data


@dataclass(frozen=True, slots=True)
class ResidualPolicy:
    """The deterministic 26-64-64-2 actor; no training runtime is required."""

    policy_id: str
    version: str
    residual_scale: float
    weights: dict[str, NDArray]

    @classmethod
    def load(cls, policy_id: str, directory: Path | None = None) -> "ResidualPolicy":
        if policy_id != "s55007_zero":
            raise ValueError(f"未注册的残差策略：{policy_id}")
        root = POLICY_DIRECTORY / policy_id if directory is None else directory
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("policy_id") != policy_id
                or manifest.get("observation_size") != 26
                or manifest.get("action_size") != 2):
            raise RuntimeError("策略清单与模型接口不一致")
        verified = {}
        for name in ("source_model", "source_summary", "protocol", "weights"):
            item = manifest[name]
            if Path(item["file"]).name != item["file"]:
                raise RuntimeError(f"策略资产路径无效：{item['file']}")
            verified[name] = _read_verified(root / item["file"], item["sha256"])
        summary = json.loads(verified["source_summary"].decode("utf-8"))
        if summary.get("actor_sha") != manifest.get("actor_sha256"):
            raise RuntimeError("策略 actor 哈希与来源摘要不一致")
        specification = manifest["weights"]
        arrays = {}
        with np.load(BytesIO(verified["weights"]), allow_pickle=False) as archive:
            if set(archive.files) != set(specification["tensors"]):
                raise RuntimeError("策略权重张量集合不一致")
            for key, shape in specification["tensors"].items():
                value = archive[key]
                if (value.shape != tuple(shape) or value.dtype != np.dtype(specification["dtype"])
                        or not np.isfinite(value).all()):
                    raise RuntimeError(f"策略权重无效：{key}")
                arrays[key] = value.copy()
        scale = manifest.get("residual_scale")
        if type(scale) not in (int, float) or scale != 0.75:
            raise RuntimeError("策略残差缩放系数必须为 0.75")
        version = manifest.get("version")
        if not isinstance(version, str) or not version:
            raise RuntimeError("策略版本无效")
        return cls(policy_id, version, float(scale), arrays)

    def predict(self, observation: ArrayLike) -> NDArray:
        value = np.asarray(observation, dtype=np.float32)
        if value.shape != (26,) or not np.isfinite(value).all():
            raise ValueError("残差策略输入必须包含 26 个有限特征")
        hidden = np.maximum(self.weights["mu.0.weight"] @ value + self.weights["mu.0.bias"], 0)
        hidden = np.maximum(self.weights["mu.2.weight"] @ hidden + self.weights["mu.2.bias"], 0)
        action = np.tanh(self.weights["mu.4.weight"] @ hidden + self.weights["mu.4.bias"])
        if action.shape != (2,) or not np.isfinite(action).all():
            raise RuntimeError("残差策略返回了无效动作")
        return action.astype(float)
