"""Validate self-contained, static float32 MLP ONNX data."""

import sys
import onnx


def validate(path):
    model = onnx.load(path, load_external_data=False)

    def all_tensors(message):
        if isinstance(message, onnx.TensorProto):
            yield message
        for field, value in message.ListFields():
            if field.type == field.TYPE_MESSAGE:
                for child in value if field.label == field.LABEL_REPEATED else (value,):
                    yield from all_tensors(child)

    tensors = all_tensors(model)
    if any(
        tensor.data_location == onnx.TensorProto.EXTERNAL or tensor.external_data
        for tensor in tensors
    ):
        raise ValueError("请上传内嵌权重的单个 ONNX 文件，不支持外部权重。")
    onnx.checker.check_model(model)
    graph = model.graph
    if len(graph.input) != 1 or len(graph.output) != 1:
        raise ValueError("模型必须只有一个输入张量和一个输出张量。")
    for value, shapes in (
        (graph.input[0], ([1, 5],)),
        (graph.output[0], ([1, 1], [1, 2])),
    ):
        info = value.type.tensor_type
        if (
            info.elem_type != onnx.TensorProto.FLOAT
            or [dimension.dim_value for dimension in info.shape.dim] not in shapes
        ):
            raise ValueError(
                "模型必须为 float32、静态输入 [1,5]，且输出尺寸须与导出的模型匹配。"
            )
    if graph.input[0].name != "raw_input":
        raise ValueError("输入名称必须为 raw_input，以匹配现有编译接口。")
    if any(node.domain not in ("", "ai.onnx") for node in graph.node):
        raise ValueError("不支持包含自定义算子的模型。")
    print("ONNX 校验通过：float32 · 静态模型 · 内嵌权重。", flush=True)


if __name__ == "__main__":
    validate(sys.argv[1])
