from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'src'),str(root/'build/lib')]
from studio import main,note,Cancelled
if __name__=='__main__':
    try:main()
    except Cancelled:sys.exit(2)
    except Exception as e:
        print(str(e),file=sys.stderr);note('HP 115nw 未完成操作',str(e));sys.exit(1)
