# 修改 tts.html api host 配置
# 安装启动 redis
# 安装 uv and python >3.10

nvidia-smi
sudo apt update
sudo apt install redis-server -y
which redis-server
sudo systemctl start redis-server
redis-cli ping
git clone https://github.com/jingyigong/Kokoro-FastAPI.git
cd Kokoro-FastAPI/
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.profile
uv --version
uv venv --python 3.10
source .venv/bin/activate
python -V
./start-gpu.sh 