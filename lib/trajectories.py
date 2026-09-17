"""Temporal behavioral units above Event and Episode."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Mapping, Optional

from .feedback_loop import Episode, EpisodeBuilder, Event


def _id(prefix: str, values: Iterable[str]) -> str:
    raw = "|".join(values).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:20]}"


@dataclass
class TaskTrajectory:
    trajectory_id: str
    agent_instance_id: str
    session_id: str
    task_id: str
    attempt_id: str
    behavioral_subject: str
    episodes: List[Episode] = field(default_factory=list)

    @property
    def events(self) -> List[Event]:
        return [event for episode in self.episodes for event in episode.events]

    def to_dict(self) -> dict[str, Any]:
        return {"trajectory_id": self.trajectory_id, "agent_instance_id": self.agent_instance_id,
                "session_id": self.session_id, "task_id": self.task_id,
                "attempt_id": self.attempt_id, "behavioral_subject": self.behavioral_subject,
                "episode_ids": [episode.episode_id for episode in self.episodes],
                "event_ids": [event.event_id for event in self.events]}


@dataclass
class SessionTrajectory:
    trajectory_id: str
    agent_instance_id: str
    session_id: str
    parent_session_id: Optional[str]
    interaction_ids: tuple[str, ...]
    tasks: List[TaskTrajectory] = field(default_factory=list)

    @property
    def events(self) -> List[Event]:
        return [event for task in self.tasks for event in task.events]

    def to_dict(self) -> dict[str, Any]:
        return {"trajectory_id": self.trajectory_id, "agent_instance_id": self.agent_instance_id,
                "session_id": self.session_id, "parent_session_id": self.parent_session_id,
                "interaction_ids": list(self.interaction_ids),
                "task_trajectories": [task.to_dict() for task in self.tasks]}


@dataclass
class InteractionTrajectory:
    """Delegation topology above individual session trajectories."""

    interaction_id: str
    session_trajectory_ids: tuple[str, ...]
    agent_instance_ids: tuple[str, ...]
    roles: tuple[str, ...]
    event_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"interaction_id": self.interaction_id,
                "session_trajectory_ids": list(self.session_trajectory_ids),
                "agent_instance_ids": list(self.agent_instance_ids),
                "roles": list(self.roles), "event_ids": list(self.event_ids)}


def build_interaction_trajectories(sessions: Iterable[SessionTrajectory]) -> list[InteractionTrajectory]:
    grouped: dict[str, dict[str, Any]] = {}
    for session in sessions:
        by_interaction: dict[str, list[Event]] = {}
        for event in session.events:
            if event.interaction_id:
                by_interaction.setdefault(event.interaction_id, []).append(event)
        for interaction_id, events in by_interaction.items():
            item = grouped.setdefault(interaction_id, {"sessions": set(), "agents": set(),
                                                       "roles": set(), "events": set()})
            item["sessions"].add(session.trajectory_id)
            item["agents"].add(session.agent_instance_id)
            item["roles"].update(event.role for event in events if event.role)
            item["events"].update(event.event_id for event in events)
    return [InteractionTrajectory(
        interaction_id=interaction_id,
        session_trajectory_ids=tuple(sorted(item["sessions"])),
        agent_instance_ids=tuple(sorted(item["agents"])),
        roles=tuple(sorted(item["roles"])),
        event_ids=tuple(sorted(item["events"])),
    ) for interaction_id, item in sorted(grouped.items())]


@dataclass
class AgentProfile:
    agent_instance_id: str
    session_trajectories: List[SessionTrajectory] = field(default_factory=list)
    interaction_trajectories: List[InteractionTrajectory] = field(default_factory=list)

    @property
    def behavioral_tendencies(self) -> dict[str, Any]:
        tasks = [task for session in self.session_trajectories for task in session.tasks]
        events = [event for task in tasks for event in task.events]
        subjects: dict[str, int] = {}
        for task in tasks:
            subjects[task.behavioral_subject] = subjects.get(task.behavioral_subject, 0) + 1
        return {
            "independent_session_count": len(self.session_trajectories),
            "independent_task_count": len(tasks),
            "episode_count": sum(len(task.episodes) for task in tasks),
            "event_count": len(events),
            "task_counts_by_behavioral_subject": subjects,
            "interaction_count": len(self.interaction_trajectories),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"agent_instance_id": self.agent_instance_id,
                "session_trajectories": [session.to_dict() for session in self.session_trajectories],
                "interaction_trajectories": [item.to_dict() for item in self.interaction_trajectories],
                "behavioral_tendencies": self.behavioral_tendencies}


def build_trajectories(events: Iterable[Event], *, window_minutes: float = 30.0) -> tuple[list[Episode], list[TaskTrajectory], list[SessionTrajectory], list[AgentProfile]]:
    builder = EpisodeBuilder(window_minutes=window_minutes)
    for event in events:
        builder.add(event)
    episodes = builder.build()
    tasks: dict[tuple[str, str, str, str, str], TaskTrajectory] = {}
    for episode in episodes:
        key = (episode.agent_instance_id, episode.session_id, episode.task_id,
               episode.attempt_id, episode.behavioral_subject)
        trajectory = tasks.setdefault(key, TaskTrajectory(
            trajectory_id=_id("task", key), agent_instance_id=key[0], session_id=key[1],
            task_id=key[2], attempt_id=key[3], behavioral_subject=key[4]))
        trajectory.episodes.append(episode)
    task_list = sorted(tasks.values(), key=lambda item: item.trajectory_id)
    sessions: dict[tuple[str, str], SessionTrajectory] = {}
    for task in task_list:
        event = task.events[0] if task.events else None
        key = (task.agent_instance_id, task.session_id)
        session = sessions.setdefault(key, SessionTrajectory(
            trajectory_id=_id("session", key), agent_instance_id=key[0], session_id=key[1],
            parent_session_id=event.parent_session_id if event else None,
            interaction_ids=tuple()))
        session.tasks.append(task)
        interaction_ids = set(session.interaction_ids)
        interaction_ids.update(event.interaction_id for event in task.events if event.interaction_id)
        session.interaction_ids = tuple(sorted(interaction_ids))
    session_list = sorted(sessions.values(), key=lambda item: item.trajectory_id)
    interaction_list = build_interaction_trajectories(session_list)
    interactions_by_agent: dict[str, list[InteractionTrajectory]] = {}
    for interaction in interaction_list:
        for agent_instance_id in interaction.agent_instance_ids:
            interactions_by_agent.setdefault(agent_instance_id, []).append(interaction)
    agents: dict[str, AgentProfile] = {}
    for session in session_list:
        profile = agents.setdefault(session.agent_instance_id, AgentProfile(session.agent_instance_id))
        profile.session_trajectories.append(session)
    for agent_instance_id, profile in agents.items():
        profile.interaction_trajectories = interactions_by_agent.get(agent_instance_id, [])
    agent_list = [agents[key] for key in sorted(agents)]
    return episodes, task_list, session_list, agent_list
