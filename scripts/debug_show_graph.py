from app.graph.workflows.minimal_chat import display_workflow_graph


if __name__ == "__main__":
    output_path = "data/eval/workflow_minimal_chat.png"
    display_workflow_graph(png_path=output_path)
    print(f"workflow graph rendered to {output_path}")

