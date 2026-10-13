"""Server-rendered project browsing, separate from queries and HTTP handlers."""
from urllib.parse import urlencode


def project_display_html(project):
    from .web import escape
    parts = ['<p class="lr-identity">'+escape(project.get('repo_label') or project['repo'] or '未分类')+'</p>',
             '<p>'+escape(project.get('display_summary', project['description']))+'</p>',
             '<p class="lr-meta">'+escape(project.get('display_note',''))+'</p>']
    if project.get('display_missing'):
        parts.append('<p class="lr-meta">中文缺项：'+escape('、'.join(project['display_missing']))+'；相应字段保留原资料。</p>')
    parts.append('<details><summary>展示来源</summary>')
    for field, label in (('title', '标题'), ('summary', '摘要')):
        source = (project.get('display_sources') or {}).get(field) or {}
        kind = {'published':'已发布保存报告','draft':'未发布拒绝草稿（内容未核验）'}.get(source.get('kind'),'原项目资料')
        link = '<a href="'+escape(source['url'])+'">相关报告</a>' if source.get('url') else ''
        parts.append('<p>'+escape(label+'：'+kind)+' '+link+'</p>')
    parts.append('</details><details><summary>项目原文简介</summary><p>'+escape(project.get('original_summary',project['description']) or '原简介未记录')+'</p>')
    original = project.get('original_summary_source') or {}
    parts.append('<p class="lr-meta">'+escape(original.get('label') or '保存的项目简介（来源未记录）')+'</p>')
    if original.get('url'):
        parts.append('<p><a href="'+escape(original['url'])+'">项目原文来源</a></p>')
    parts.append('</details>')
    return ''.join(parts)


def project_title_class(project):
    source = (project.get('display_sources') or {}).get('title') or {}
    return 'lr-project-title' if source.get('kind') in ('published','draft') else 'lr-project-identity-title'


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
            body.append('<div class="card" data-lr-role="project" data-repo="'+escape(repo)+'"><h2 class="'+project_title_class(project)+'"><a href="/history?'+escape(urlencode({'project':repo}))+'">'+escape(project.get('display_title') or repo or '未分类')+'</a></h2>')
            body.append(project_display_html(project)+'<p>标签：'+escape('、'.join(labels) or '未分类')+'</p>')
            body.append('<p>最新结果：'+escape(report_label(latest['report_status']))+' · '+escape(latest['date'])+'</p></div>')
        if not projects:
            body.append('<p>没有匹配项目。</p>')
    else:
        project = next((p for p in projects if p['repo'] == selected), None)
        body.append('<p><a href="/history">← 全部项目</a></p><h2 class="'+project_title_class(project or {})+'">'+escape((project or {}).get('display_title') or selected or '未分类')+'</h2>')
        if project:
            if selected:
                body.append('<a href="https://github.com/'+escape(selected)+'">项目仓库</a>')
            body.append(project_display_html(project)+'<h3>标签与来源</h3>')
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
