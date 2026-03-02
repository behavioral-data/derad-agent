from pathlib import Path
from setuptools import find_packages, setup


ROOT = Path(__file__).resolve().parent


def _read_requirements() -> list[str]:
    req_path = ROOT / "requirements.txt"
    lines = []
    for raw in req_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()
        lines.append(line)
    return lines


setup(
    name="derad-agent",
    version="2.0.0",
    packages=find_packages(),
    install_requires=_read_requirements(),
    include_package_data=True,
    python_requires=">=3.9",
    author="Advait MB",
    description="Single-pass Community Notes retrieval and misleadingness landscape scoring.",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Operating System :: OS Independent",
    ],
    entry_points={
        "console_scripts": [
            "derad-onboard-data=derad_agent.cli.onboard_data:main",
        ]
    },
)