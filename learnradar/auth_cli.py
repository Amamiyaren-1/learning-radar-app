"""Local hidden account initialization/reset and session revocation. No networking."""
import argparse
import getpass
import sys
import warnings
from pathlib import Path

from .auth import password_hash
from .auth_store import AuthStore
from .config import load_settings, ConfigError
from .tasks import TaskQueue, TaskError


def main(argv=None):
    parser = argparse.ArgumentParser(description='本机唯一网页账号；密码不接受命令参数')
    parser.add_argument('action', choices=('init', 'reset', 'revoke-sessions'))
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--username', default='owner')
    args = parser.parse_args(argv)
    settings = load_settings()
    directory = (args.data_dir or settings.data_dir).resolve()
    try:
        TaskQueue(directory, demo=False)  # Existing persistent mode guard; never claims work.
        repo = AuthStore(directory / 'learning-radar.sqlite')
        if args.action == 'revoke-sessions':
            repo.revoke_all()
            print('所有网页登录会话已撤销；不分析、不发送通知。')
            return 0
        if not args.username or len(args.username) > 128:
            raise ValueError('账号名无效')
        if args.action == 'init' and repo.account():
            raise ValueError('账号已初始化；请使用 reset')
        if not sys.stdin.isatty():
            raise ValueError('必须在本机交互终端隐藏输入密码；拒绝重定向输入')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass('网页密码（隐藏输入，12 至 128 字符）：')
            confirmation = getpass.getpass('再次输入网页密码：')
        if password != confirmation:
            raise ValueError('两次输入不一致，未保存')
        verifier = password_hash(password)
        repo.set_account(args.username, verifier, fixture=False, reset=args.action == 'reset')
        print('网页账号已保存（scrypt N=32768 r=8 p=3）；旧会话已撤销。不分析、不发送通知。')
        return 0
    except (ConfigError, TaskError, ValueError) as exc:
        print(str(exc))
        return 1
    except (KeyboardInterrupt, EOFError, getpass.GetPassWarning):
        print('输入已取消或终端不能隐藏输入；未保存密码。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
