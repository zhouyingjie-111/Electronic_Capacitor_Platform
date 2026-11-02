"""Explicit sample graph for backend and browser integration tests."""


def buck_plan():
    def block(ref, kind, **params):
        return {
            "id": ref,
            "name": ref,
            "type": kind,
            "parameters": [{"name": k, "value": v} for k, v in params.items()],
        }

    def wire(source, target, port=1):
        return {
            "source": source,
            "source_port": 1,
            "target": target,
            "target_port": port,
        }

    return {
        "title": "Buck 平均模型",
        "description": "按电感电流与输出电压状态方程搭建连续导通模式平均模型：L·di/dt = d·Vin − Vo，C·dVo/dt = iL − Vo/R。用于低频动态分析，不包含开关纹波和器件损耗。示例参数可在模型工作区调整。",
        "variables": [
            {"name": k, "value": v}
            for k, v in {
                "Vin": 24,
                "duty": 0.5,
                "L": 1e-3,
                "C": 470e-6,
                "R": 10,
            }.items()
        ],
        "blocks": [
            block("InputVoltage", "Constant", Value="Vin"),
            block("DutyRatio", "Constant", Value="duty"),
            block("AverageVoltage", "Product", Inputs="2"),
            block("InductorVoltage", "Sum", Inputs="+-"),
            block("InverseL", "Gain", Gain="1/L"),
            block("InductorCurrent", "Integrator", InitialCondition="0"),
            block("CapacitorCurrent", "Sum", Inputs="+-"),
            block("InverseC", "Gain", Gain="1/C"),
            block("OutputVoltage", "Integrator", InitialCondition="0"),
            block("LoadCurrent", "Gain", Gain="1/R"),
            block("VoltageCurrent", "Scope", NumInputPorts="2"),
        ],
        "connections": [
            wire("InputVoltage", "AverageVoltage"),
            wire("DutyRatio", "AverageVoltage", 2),
            wire("AverageVoltage", "InductorVoltage"),
            wire("OutputVoltage", "InductorVoltage", 2),
            wire("InductorVoltage", "InverseL"),
            wire("InverseL", "InductorCurrent"),
            wire("InductorCurrent", "CapacitorCurrent"),
            wire("LoadCurrent", "CapacitorCurrent", 2),
            wire("CapacitorCurrent", "InverseC"),
            wire("InverseC", "OutputVoltage"),
            wire("OutputVoltage", "LoadCurrent"),
            wire("OutputVoltage", "VoltageCurrent"),
            wire("InductorCurrent", "VoltageCurrent", 2),
        ],
    }
