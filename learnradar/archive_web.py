"""Server-rendered project browsing, separate from queries and HTTP handlers."""
from urllib.parse import urlencode


def render_projects(projects, query, demo=False):
    from .web import page, escape, demo_banner, report_label, notification_label, TASK_STATES, RUN_STATES
    from .feedback import ITEMS, CHOICES
    keyword = (query.get('q') or [''])[0]
    selected = (query.get('project') or [None])[0]
    tags = query.get('tag', [])
    body = [demo_banner(demo), '<h1>项目历史</h1>', '<p>项目 → 历次报告；标签只用于归档，不推断长期偏好。</p>']
    if selected is None:
        body.append('<form data-lr-action="search" method="get" action="/history"><label>项目名称或简介关键词 <input name="q" value="'+escape(keyword)+'"></label>')
        body.append('<label>标签（逗号分隔，全部匹配） <input name="tags" value="'+escape(','.join(tags))+'"></label><button type="submit">查找项目</button></form>')
        for project in projects:
            repo = project['repo']
            labels = sorted({t['label'] for t in project['tags']})
            latest = project['reports'][0]
            body.append('<div class="card" data-lr-role="project" data-repo="'+escape(repo)+'"><h2><a href="/history?'+escape(urlencode({'project':repo}))+'">'+escape(repo or '未分类')+'</a></h2>')
            body.append('<p>'+escape(project['description'])+'</p><p>标签：'+escape('、'.join(labels) or '未分类')+'</p>')
            body.append('<p>最新结果：'+escape(report_label(latest['report_status']))+' · '+escape(latest['date'])+'</p></div>')
        if not projects:
            body.append('<p>没有匹配项目。</p>')
    else:
        project = next((p for p in projects if p['repo'] == selected), None)
        body.append('<p><a href="/history">← 全部项目</a></p><h2>'+escape(selected or '未分类')+'</h2>')
        if project:
            if selected:
                body.append('<a href="https://github.com/'+escape(selected)+'">项目仓库</a>')
            body.append('<p>'+escape(project['description'])+'</p><h3>标签与来源</h3>')
            if not project['tags']:
                body.append('<p>未分类：没有可靠的保存标签。</p>')
            for tag in project['tags']:
                source = 'GitHub topics（项目方元数据）' if tag['source']=='github' else '助手分类（结构检查通过，内容未全面核验）'
                body.append('<details><summary>'+escape(tag['label'])+' · '+escape(source)+'</summary><p>'+escape(tag['basis'])+'</p><p>关联运行：'+escape(tag['run_id'])+'；证据：'+escape(tag['evidence_ids'])+'</p></details>')
            body.append('<h3>历次报告</h3>')
            for row in project['reports']:
                title = escape(row['title'] or '保存的运行记录')
                if row['report_id']:
                    title = '<a href="/report/'+escape(row['run_id'])+'">'+title+'</a>'
                elif row['task_id']:
                    title = '<a href="/task/'+escape(row['task_id'])+'">'+title+'</a>'
                processing = TASK_STATES.get(row['task_status']) or RUN_STATES.get(row['run_status'], '旧记录未保存处理状态')
                body.append('<div class="card" data-lr-role="report" data-run-id="'+escape(row['run_id'])+'"><strong>'+title+'</strong><p>'+escape(row['date'])+' · '+escape(report_label(row['report_status']))+'</p><p>任务：'+escape(processing)+' · 通知：'+escape(notification_label(row['email_status']))+'</p>')
                if row['report_status']=='rejected':
                    body.append('<p>未发布草稿；助手分类未采用，原拒绝原因保留在报告页。</p>')
                if row['email_status']=='unknown':
                    body.append('<p>通知结果不明，请人工核对，不自动重发。</p>')
                if row['notes'] and not row['report_id']:
                    body.append('<p>'+escape(row['notes'])+'</p>')
                if row['feedback_at']:
                    choices = dict(CHOICES)
                    body.append('<p>已有反馈：'+'；'.join(escape(label)+'：'+escape(choices.get(row[key],'未填写')) for key,label in ITEMS)+'</p><p>'+escape(row['note'] or '')+'</p>')
                sources = row.get('discovery_sources', [])
                directions = '、'.join(source['direction'] for source in sources) or row['topic'] or '未记录'
                mode = '广泛发现' if row.get('discovery_mode') == 'broad' else '指定方向/旧运行'
                body.append('<details><summary>发现来源与技术详情</summary><p>'+escape(mode)+'；搜索方向（不是标签）：'+escape(directions)+'；运行编号：'+escape(row['run_id'])+'</p>')
                for source in sources:
                    body.append('<p>保存的查询：'+escape(source.get('query',''))+'</p>')
                body.append('</details></div>')
    from .ui_projection import projects_data
    return page('Learning Radar 项目历史', '\n'.join(body), kind='projects' if selected is None else 'project',
                data=projects_data(projects,selected),fixture=demo)
