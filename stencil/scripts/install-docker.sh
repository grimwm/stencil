#!/bin/sh

# Docker Installation Script
# Detects OS and installs Docker Desktop appropriately
# POSIX-compliant for Linux and macOS

set -e

echo "Detecting operating system..."

# Check if Docker is already available
if command -v docker >/dev/null 2>&1; then
    echo "Docker is already installed and available!"
    docker --version
    echo "No installation needed."
    exit 0
fi

OS_TYPE=$(uname -s)

case "$OS_TYPE" in
    Linux)
        echo "Linux detected. Installing Docker..."

        # Detect Linux distribution
        if command -v apt-get >/dev/null 2>&1; then
            # Ubuntu/Debian
            echo "Ubuntu/Debian detected. Installing Docker..."
            sudo apt-get update
            sudo apt-get install -y ca-certificates curl gnupg
            sudo install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
            # shellcheck disable=SC1091
            ARCH=$(dpkg --print-architecture)
            . /etc/os-release
            echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            sudo apt-get update
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

        elif command -v dnf >/dev/null 2>&1; then
            # Fedora
            echo "Fedora detected."

            # Check for podman-docker conflict
            if rpm -q podman-docker >/dev/null 2>&1; then
                echo "WARNING: podman-docker is installed, which conflicts with Docker CE."
                echo "To install Docker CE, you may need to remove podman-docker first:"
                echo "  sudo dnf remove podman-docker"
                echo "Then re-run this script."
                echo ""
                echo "Alternatively, if you prefer to use podman instead of Docker,"
                echo "you can modify the docker-compose.yml to use podman-compose."
                exit 1
            fi

            echo "Installing Docker..."
            sudo dnf -y install dnf-plugins-core
            # Add Docker repository
            sudo tee /etc/yum.repos.d/docker-ce.repo > /dev/null <<'EOF'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://download.docker.com/linux/fedora/$releasever/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://download.docker.com/linux/fedora/gpg
EOF
            sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

        elif [ -f /etc/redhat-release ] || [ -f /etc/centos-release ] || [ -f /etc/rocky-release ]; then
            # CentOS/RHEL/Rocky Linux (modern dnf-based systems)
            echo "CentOS/RHEL/Rocky Linux detected. Installing Docker..."
            sudo dnf -y install dnf-plugins-core
            sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
            sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
        else
            echo "Unsupported Linux distribution. Please install Docker manually from https://docs.docker.com/get-docker/"
            exit 1
        fi

        # Start Docker service
        sudo systemctl start docker
        sudo systemctl enable docker

        echo "Docker installed successfully! You may need to log out and back in for group changes to take effect."
        ;;

    Darwin)
        echo "macOS detected."

        # Check if Homebrew is installed
        if ! command -v brew >/dev/null 2>&1; then
            echo "Homebrew not found. Installing Homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi

        echo "Installing Docker Desktop for Mac..."
        brew install --cask docker

        echo "Docker Desktop installed! Please start Docker Desktop from your Applications folder."
        ;;

    *)
        echo "Unsupported operating system: $OS_TYPE"
        echo "Please install Docker manually from https://docs.docker.com/get-docker/"
        exit 1
        ;;
esac

echo "Docker installation completed!"
echo "Test with: docker --version"
