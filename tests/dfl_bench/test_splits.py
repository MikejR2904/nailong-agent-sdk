import pytest
from dfl_bench.instances import Instance, generate_instance
from dfl_bench.live.scenarios import FAMILIES, generate_task
from dfl_bench.splits import (
    CONTENT_KEYS,
    assert_no_leakage,
    group_instances,
    leakage_report,
    split_instances,
)


def world(templates=8, per=3, seed=1):
    return [
        generate_instance(f"W{seed}x{t}", "aligned", i, seed)
        for t in range(templates)
        for i in range(per)
    ]


def live_world(seed=4000, variants=6, per=6):
    return [
        generate_task(family, variant, index, seed).instance
        for family in FAMILIES
        for variant in range(variants)
        for index in range(per)
    ]


def test_instances_of_one_template_are_never_split_apart():
    instances = world()
    train, val, test = split_instances(instances, (0.5, 0.2, 0.3), seed=3)
    assert_no_leakage(train, val, test)
    assert len(train) + len(val) + len(test) == len(instances)
    for template in {i.template_id for i in instances}:
        homes = {
            n
            for n, part in enumerate((train, val, test))
            if any(i.template_id == template for i in part)
        }
        assert len(homes) == 1


def test_a_shared_task_digest_glues_different_templates_into_one_group():
    first = generate_instance("A", "aligned", 0, 1)
    second = generate_instance("B", "aligned", 0, 1)
    twin = Instance(**{**second.__dict__, "query": first.query})
    assert len(group_instances([first, twin])) == 1


def test_leakage_detector_names_the_shared_key():
    instances = world(4, 2)
    leaked = [instances[0]]
    report = leakage_report(instances[:4], leaked + instances[4:])
    assert "instance" in report and "template" in report
    with pytest.raises(ValueError, match="Training/test leakage"):
        assert_no_leakage(instances[:4], leaked)


def test_split_fractions_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        split_instances(world(2, 1), (0.5, 0.5, 0.5), seed=0)


def test_split_is_deterministic_for_a_seed():
    instances = world()
    a = split_instances(instances, (0.6, 0.2, 0.2), seed=5)
    b = split_instances(instances, (0.6, 0.2, 0.2), seed=5)
    assert [[i.instance_id for i in p] for p in a] == [[i.instance_id for i in p] for p in b]


def test_unknown_keys_are_rejected_by_name():
    with pytest.raises(ValueError, match=r"Unknown split keys \['topology'\]"):
        group_instances(world(2, 1), ("template", "topology"))


def test_topology_alone_glues_fixed_shape_families_into_one_giant_group():
    tasks = live_world()
    coarse = group_instances(tasks, ("template", "graph", "query"))
    assert max(len(g) for g in coarse) > len(tasks) // 2
    with pytest.raises(ValueError, match="too coarse"):
        split_instances(tasks, (0.6, 0.15, 0.25), 4000, ("template", "graph", "query"))


def test_content_keys_keep_templates_whole_and_give_usable_split_sizes():
    tasks = live_world()
    train, val, test = split_instances(tasks, (0.6, 0.15, 0.25), 4000, CONTENT_KEYS)
    assert_no_leakage(train, val, test, keys=(*CONTENT_KEYS, "instance"))
    assert len(train) + len(val) + len(test) == len(tasks)
    assert len(val) >= 10 and len(test) >= 20
    for template in {t.template_id for t in tasks}:
        homes = {
            n
            for n, part in enumerate((train, val, test))
            if any(i.template_id == template for i in part)
        }
        assert len(homes) == 1


def test_an_exact_duplicate_history_is_glued_even_under_another_template_name():
    first = live_world(variants=1, per=1)[0]
    twin = Instance(**{**first.__dict__, "template_id": "renamed", "instance_id": "twin"})
    assert first.content_hash() == twin.content_hash()
    assert len(group_instances([first, twin], CONTENT_KEYS)) == 1
    report = leakage_report([first], [twin], keys=("content",))
    assert report == {"content": [first.content_hash()]}
