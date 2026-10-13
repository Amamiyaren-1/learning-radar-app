"""历史读取视图：只使用保存字段，缺失归属保留为未分类，不从编号猜测。"""
import json
import re
from pathlib import Path
from .evidence import normalize_repo_name

UNCATEGORIZED=""


def object_value(raw):
    try:
        value=json.loads(raw or "{}")
        return value if isinstance(value,dict) else {}
    except (ValueError,TypeError): return {}


def history_records(store, topics):
    known={str(topic["id"]) for topic in topics}
    rows=store.conn.execute("""
        WITH ids AS (SELECT run_id FROM runs UNION SELECT run_id FROM reports UNION SELECT run_id FROM tasks)
        SELECT ids.run_id,r.status AS run_status,r.started_at,r.finished_at,r.notes,r.config_snapshot,
               p.report_id,p.created_at,p.status AS report_status,p.title,p.payload,
               t.task_id,t.status AS task_status,t.topic AS task_topic,t.kind,t.created_at AS task_created_at,t.payload AS task_payload,
               e.status AS email_status,e.error AS email_error,
               (SELECT CASE WHEN COUNT(DISTINCT topic)=1 THEN MAX(topic) END FROM cycles WHERE run_id=ids.run_id AND topic<>'') AS cycle_topic,
               f.understood_what,f.understood_highlight,f.want_more,f.note,f.updated_at AS feedback_at
        FROM ids LEFT JOIN runs r ON r.run_id=ids.run_id
        LEFT JOIN reports p ON p.run_id=ids.run_id
        LEFT JOIN tasks t ON t.run_id=ids.run_id
        LEFT JOIN emails e ON e.rowid=(SELECT rowid FROM emails WHERE run_id=ids.run_id ORDER BY rowid DESC LIMIT 1)
        LEFT JOIN feedback f ON f.run_id=ids.run_id
        ORDER BY COALESCE(p.created_at,r.started_at,t.created_at,'') DESC,ids.run_id DESC
        """).fetchall()
    result=[]
    root=store.path.parent/"runs"
    for raw in rows:
        row=dict(raw)
        # 只读取本数据目录内的旧上下文；缺失/损坏不猜测，也不回写。
        context={}
        path=root/row["run_id"]/"run-context.json"
        if path.resolve().is_relative_to(root.resolve()):
            try: context=object_value(path.read_text(encoding="utf-8"))
            except (OSError,UnicodeError): pass
        snapshot=object_value(row.pop("config_snapshot"))
        payload=object_value(row.pop("payload"))
        task_input=object_value(row.pop("task_payload"))
        topic=(row["task_topic"] if row["kind"]!="replay_analysis" else None) or context.get("topic") or snapshot.get("topic") or row["cycle_topic"]
        row["topic"]=topic if isinstance(topic,str) and topic in known else UNCATEGORIZED
        row["discovery_mode"]=task_input.get("discovery_mode") or context.get("discovery_mode") or snapshot.get("discovery_mode")
        # Distinguish the saved query plan from the actual sources of this project.
        # Source rows were persisted with discovered candidates, not inferred from tags.
        row["discovery_sources"]=[]
        target=payload.get("target")
        repo=target.get("repo") if isinstance(target,dict) else None
        value=repo or context.get("target")
        project=normalize_repo_name(value) if isinstance(value,str) else ""
        row["project"]=project if re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+",project) else UNCATEGORIZED
        from .archive import saved_report_display
        row['_saved_display'] = saved_report_display(row, payload)
        if (not row['_saved_display']['repo_label'] and isinstance(value, str)
                and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value.strip())):
            row['_saved_display']['repo_label'] = value.strip()
        if row["project"]:
            for candidate in store.conn.execute('SELECT full_name,raw FROM candidates WHERE run_id=?', (row['run_id'],)):
                if normalize_repo_name(candidate['full_name']) != row["project"]:
                    continue
                sources=object_value(candidate['raw']).get('discovery_sources', [])
                if isinstance(sources,list):
                    for source in sources:
                        if isinstance(source,dict) and isinstance(source.get('direction'),str) and source not in row["discovery_sources"]:
                            row["discovery_sources"].append(source)
        row["date"]=row["created_at"] or row["finished_at"] or row["started_at"] or row["task_created_at"] or "日期未记录"
        result.append(row)
    return result
