import pytest

from aiws.core import Workspace, put_json
from aiws.git_store import GitStore
from aiws.jobs import Jobs
from aiws.retrieval import Retrieval
from aiws.service import Service


@pytest.mark.parametrize("job_count", [20, 2000])
def test_resume_reads_only_one_job_and_never_refreshes_index(tmp_path, monkeypatch, job_count):
    workspace = Workspace(tmp_path)
    workspace.init()
    project = workspace.project_create("Resume example")
    pid = project["project_id"]
    jobs = Jobs(workspace)
    scope = dict(include_company=False, include_professional=False, project_ids=[pid], topic_ids=[])
    job = jobs.create("Draft", "Finish editing", scope)
    service = Service(tmp_path)
    job_id = job["job_id"]
    service.call("bind_session", {"session_id": "continue-here", "jid": job_id})
    service.call("write_work_file", dict(jid=job_id, project_id=pid, name="draft.md", content="chapter one"))
    draft = jobs.get(job_id)["artifacts"][0]["path"]
    GitStore(workspace, pid).initialize()
    job = jobs.get(job_id)
    service.call("save_work_checkpoint", dict(
        jid=job_id, revision=job["revision"], context_patch={"working_on": "chapter two"},
        patch={"next_steps": ["Check citations"]},
    ))
    raw = jobs.get(job_id)
    for number in range(2, job_count + 1):
        other_id = f"JOB-{number:04}"
        put_json(jobs.file(other_id), {**raw, "job_id": other_id})

    get = Jobs.get
    reads = []

    def track_read(self, jid):
        reads.append(jid)
        return get(self, jid)

    def unexpected(*args, **kwargs):
        pytest.fail("Targeted resume must not list jobs or update the index")

    monkeypatch.setattr(Jobs, "get", track_read)
    monkeypatch.setattr(Jobs, "query", unexpected)
    monkeypatch.setattr(Retrieval, "update", unexpected)
    for operation, args in [
        ("resume_job", {"jid": job_id}),
        ("resume_job", {"jid": job_id, "detail": "full"}),
        ("resume_session", {"session_id": "continue-here"}),
    ]:
        reads.clear()
        result = service.call(operation, args)
        assert reads == [job_id]
        work = result["work_context"]
        assert work["task_context"]["context"]["working_on"] == "chapter two"
        assert work["task_context"]["next_steps"] == ["Check citations"]
        assert work["primary_draft"]["path"] == draft.removeprefix(project["path"] + "/")
        assert work["project_brief"]["expand"]["operation"] == "get_project_overview"
        if args.get("detail") == "full":
            assert result["artifact_integrity"][0]["integrity"] == "ok"


def test_resume_flags_changed_and_missing_reviewed_files(tmp_path):
    service = Service(tmp_path)
    service.call("init", {})
    project = service.call("create_project", {"name": "Source changes"})
    pid = project["project_id"]
    job = service.call("create_job", dict(title="Read sources", goal="Draft report",
        scope=dict(include_company=False, include_professional=False, project_ids=[pid], topic_ids=[])))
    args = {"jid": job["job_id"]}
    root = service.w.path(project["path"])
    paths = ["资料/changed.txt", "资料/deleted.txt"]
    related_other_draft = "工作/JOB-0002_Other/工作稿/related.md"
    unrelated_other_draft = "工作/JOB-0002_Other/工作稿/unrelated.md"
    for path in paths:
        (root / path).write_text("Original")
    for path in (related_other_draft, unrelated_other_draft):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("Draft")
    service.call("initialize_project_git", {"project_id": pid})
    view = service.call("resume_job", args)["work_context"]
    assert unrelated_other_draft not in [r["path"] for r in view["reading_queue"]]
    assert unrelated_other_draft not in [r["path"] for r in view["git_changes"]]
    project_view = service.call("prepare_work_context", {**args, "detail": "full"})
    assert unrelated_other_draft in [r["path"] for r in project_view["reading_queue"]]
    service.call("mark_context_read", {**args, "observation_commit": view["observation"]["commit"],
                                      "paths": paths + [related_other_draft]})
    assert service.call("resume_job", args)["work_context"]["changed_reviewed_files"] == []
    (root / paths[0]).write_text("Revised")
    (root / paths[1]).unlink()
    (root / related_other_draft).write_text("Revised")
    resumed = service.call("resume_job", args)["work_context"]
    changed = resumed["changed_reviewed_files"]
    assert {r["path"]: r["state"] for r in changed} == {
        paths[0]: "changed", paths[1]: "missing", related_other_draft: "changed"}
    assert related_other_draft in [r["path"] for r in resumed["reading_queue"]]


def test_resume_in_local_mode_without_git_still_returns_task_state(tmp_path):
    service = Service(tmp_path)
    service.call("init", {})
    project = service.call("create_project", {"name": "Local example"})
    pid = project["project_id"]
    job = service.call("create_job", dict(title="Draft", goal="Finish", scope=dict(
        include_company=False, include_professional=False, project_ids=[pid], topic_ids=[])))
    service.call("write_work_file", dict(jid=job["job_id"], project_id=pid, name="draft.md", content="Draft"))
    result = service.call("resume_job", {"jid": job["job_id"]})
    assert result["work_context"]["task_context"]["title"] == "Draft"
    assert result["work_context"]["primary_draft"]["path"].endswith("/draft.md")
    assert result["work_context"]["primary_draft"]["state"] == "not_checked"
    assert not result["work_context"]["git_status"]["enabled"]
