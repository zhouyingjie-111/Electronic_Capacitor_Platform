function output = portal_build_model(configJSON)
%PORTAL_BUILD_MODEL Build only a server-validated, new signal-flow model.
% Structural operations always go through the MathWorks model_edit toolkit.
cfg = jsondecode(configJSON);
settings = library.settingsLookup();
if settings.found || ~settings.gatePass
    error('Portal:LibraryPolicy', 'Current custom library policy needs review before automated construction.');
end
loaded = find_system('Type','block_diagram');
for k = 1:numel(loaded)
    if strcmp(get_param(loaded{k},'BlockDiagramType'),'model') && ...
            ~strcmp(get_param(loaded{k},'SimulationStatus'),'stopped')
        error('Portal:ModelBusy','An existing model is running.');
    end
end
model = cfg.model_name;
if bdIsLoaded(model) || isfile(cfg.model_path)
    error('Portal:ModelExists','Refusing to replace an existing model.');
end
new_system(model,'Model');
open_system(model);
initial = char(model_read(model,'root','0'));
if isempty(regexp(initial,'(?m)^status: ok\s*$','once'))
    error('Portal:ReadFailed','Cannot inspect new model.');
end
workspace = get_param(model,'ModelWorkspace');
for k = 1:numel(cfg.variables)
    assignin(workspace,cfg.variables(k).name,cfg.variables(k).value);
end
% The configuration target is generated locally, never supplied by the LLM.
configuration = struct('op','configure','target',['config:' model], ...
    'params',struct('StopTime','0.01'));
% Avoid heterogeneous struct concatenation: append JSON objects as text.
operationJSON = cfg.operations;
operationJSON = [operationJSON(1:end-1) ',' jsonencode(configuration) ']'];
editResult = char(model_edit(model,'root',operationJSON,'full'));
% Read and check immediately even if an operation only partially succeeded.
structure = char(model_read(model,'root','0'));
checks = char(model_check(model,'root','["all"]'));
if isempty(regexp(editResult,'(?m)^status: ok\s*$','once')) || ...
        isempty(regexp(structure,'(?m)^status: ok\s*$','once')) || ...
        isempty(regexp(checks,'(?m)^status: healthy\s*$','once'))
    output = jsonencode(struct('built',false,'edit_report',editResult, ...
        'structure_report',structure,'check_report',checks));
    return
end
if ~strcmp(get_param(model,'SimulationStatus'),'stopped')
    error('Portal:UnexpectedSimulation','Model is not idle.');
end
save_system(model,cfg.model_path);
open_system(model);
drawnow;
output = jsonencode(struct('built',true,'model',model,'simulation_started',false, ...
    'simulation_status','stopped','edit_report',editResult, ...
    'structure_report',structure,'check_report',checks));
end
