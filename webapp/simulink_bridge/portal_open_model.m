function result = portal_open_model(configuration)
% Open a project model copy for viewing. No simulation object or run command.
cfg = jsondecode(configuration);
loaded = find_system('Type', 'block_diagram');
for k = 1:numel(loaded)
    if strcmp(get_param(loaded{k}, 'BlockDiagramType'), 'model') && ...
            ~strcmp(get_param(loaded{k}, 'SimulationStatus'), 'stopped')
        error('Portal:Busy', 'A model is already running; viewing was not started.');
    end
end
if isempty(cfg.model_path)
    model = cfg.model_name;
    new_system(model, 'Model');
else
    load_system(cfg.model_path);
    [~, model] = fileparts(cfg.model_path);
end
open_system(model);
set_param(model, 'ZoomFactor', 'FitSystem');
drawnow;
state = get_param(model, 'SimulationStatus');
if ~strcmp(state, 'stopped')
    error('Portal:ViewState', 'The model is not idle.');
end
result = jsonencode(struct('opened', strcmp(get_param(model, 'Open'), 'on'), ...
    'model', model, 'simulation_status', state, ...
    'simulation_time', double(get_param(model, 'SimulationTime')), ...
    'simulation_started', false));
end
