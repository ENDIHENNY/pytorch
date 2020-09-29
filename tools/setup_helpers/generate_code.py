import argparse
import os
import sys

source_files = {'.py', '.cpp', '.h'}

DECLARATIONS_PATH = 'torch/share/ATen/Declarations.yaml'

# TODO: This is a little inaccurate, because it will also pick
# up setup_helper scripts which don't affect code generation
def all_generator_source():
    r = []
    for directory, _, filenames in os.walk('tools'):
        for f in filenames:
            if os.path.splitext(f)[1] in source_files:
                full = os.path.join(directory, f)
                r.append(full)
    return sorted(r)


def generate_code(ninja_global=None,
                  declarations_path=None,
                  nn_path=None,
                  install_dir=None,
                  subset=None,
                  disable_autograd=False,
                  force_schema_registration=False,
                  operator_selector=None):
    from tools.autograd.gen_autograd import gen_autograd, gen_autograd_python
    from tools.autograd.gen_annotated_fn_args import gen_annotated
    from tools.jit.gen_unboxing_wrappers import gen_unboxing_wrappers
    from tools.codegen.selective_build.selector import SelectiveBuilder


    # Build ATen based Variable classes
    if install_dir is None:
        install_dir = 'torch/csrc'
        python_install_dir = 'torch/testing/_internal/generated'
    else:
        python_install_dir = install_dir
    autograd_gen_dir = os.path.join(install_dir, 'autograd', 'generated')
    jit_gen_dir = os.path.join(install_dir, 'jit', 'generated')
    for d in (autograd_gen_dir, jit_gen_dir, python_install_dir):
        if not os.path.exists(d):
            os.makedirs(d)
    runfiles_dir = os.environ.get("RUNFILES_DIR", None)
    data_dir = os.path.join(runfiles_dir, 'pytorch') if runfiles_dir else ''
    autograd_dir = os.path.join(data_dir, 'tools', 'autograd')
    tools_jit_templates = os.path.join(data_dir, 'tools', 'jit', 'templates')

    if subset == "pybindings" or not subset:
        gen_autograd_python(declarations_path or DECLARATIONS_PATH, autograd_gen_dir, autograd_dir)

    if operator_selector is None:
        operator_selector = SelectiveBuilder.get_nop_selector()

    if subset == "libtorch" or not subset:

        gen_autograd(
            declarations_path or DECLARATIONS_PATH,
            autograd_gen_dir,
            autograd_dir,
            disable_autograd=disable_autograd,
            operator_selector=operator_selector,
        )
        gen_unboxing_wrappers(
            declarations_path or DECLARATIONS_PATH,
            jit_gen_dir,
            tools_jit_templates,
            disable_autograd=disable_autograd,
            operator_selector=operator_selector,
            force_schema_registration=force_schema_registration)

    if subset == "python" or not subset:
        gen_annotated(
            declarations_path or DECLARATIONS_PATH,
            python_install_dir,
            autograd_dir)

def get_selector(selected_op_list, selected_op_list_path):
    # cwrap depends on pyyaml, so we can't import it earlier
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, root)
    from tools.autograd.utils import load_op_list_and_strip_overload
    from tools.codegen.selective_build.selector import SelectiveBuilder

    selected_op_list = load_op_list_and_strip_overload(
        selected_op_list,
        selected_op_list_path,
    )

    is_root_operator = False
    is_used_for_training = False

    if selected_op_list_path is not None:
        # This is a temporary hack (which will be cleaned up very soon)
        # to be able to determine which invocations are for what type
        # of operation. We force the use of the file name to determine
        # which use-case is being catered to for this specific
        # invocation of generate_code.py
        #
        # For the internal build, we have fixed things up to only use
        # the file name based operator selection strategy, and the open
        # source version doesn't seem to use --selected-op-list at all
        # and uses --selected-op-list path instead.
        #
        # ./caffe2/CMakeLists.txt:
        # $<$<BOOL:${SELECTED_OP_LIST}>:--selected-op-list-path="${SELECTED_OP_LIST}">
        #
        # Please help me verify the following claims:
        # -------------------------------------------
        # 1. Currently, OSS doesn't support on-device learning, so there is
        # no support for VariableType_N.cpp code-gen in OSS
        #
        # 2. OSS uses the full closure for unboxing wrappers code-gen, so
        # it generates more unboxing wrappers than needed compared to
        # the internal build.
        #
        # If we are in internal FB build, then we will expect one of the 2
        # file paths below.
        if "root_op_list.yaml" in selected_op_list_path or "combined_op_list.yaml" in selected_op_list_path:
            is_root_operator = "root_op_list.yaml" in selected_op_list_path
            is_used_for_training = "combined_op_list.yaml" in selected_op_list_path
        else:
            # Else, this is most likely OSS, so enable things to keep the
            # behaviour consistent.
            is_root_operator = True
            is_used_for_training = True

    selector: SelectiveBuilder = SelectiveBuilder.get_nop_selector()
    if selected_op_list is not None:
        selector = SelectiveBuilder.from_legacy_op_registration_allow_list(
            selected_op_list,
            is_root_operator,
            is_used_for_training,
        )

    return selector


def main():
    parser = argparse.ArgumentParser(description='Autogenerate code')
    parser.add_argument('--declarations-path')
    parser.add_argument('--nn-path')
    parser.add_argument('--ninja-global')
    parser.add_argument('--install_dir')
    parser.add_argument(
        '--subset',
        help='Subset of source files to generate. Can be "libtorch" or "pybindings". Generates both when omitted.'
    )
    parser.add_argument(
        '--disable-autograd',
        default=False,
        action='store_true',
        help='It can skip generating autograd related code when the flag is set',
    )
    parser.add_argument(
        '--selected-op-list-path',
        help='Path to the yaml file that contains the list of operators to include for custom build.',
    )
    # parser.add_argument(
    #     '--selected-op-list',
    #     nargs="*",
    #     type=str,
    #     help="""List of operator names to include for custom build, in addition to those in selected-op-list-path.
    #     For example, --selected-op-list aten::add.Tensor aten::_convolution.""",
    # )
    parser.add_argument(
        '--force_schema_registration',
        action='store_true',
        help='force it to generate schema-only registrations for ops that are not'
        'listed on --selected-op-list'
    )
    options = parser.parse_args()

    generate_code(
        options.ninja_global,
        options.declarations_path,
        options.nn_path,
        options.install_dir,
        options.subset,
        options.disable_autograd,
        options.force_schema_registration,
        # options.selected_op_list
        operator_selector=get_selector(None, options.selected_op_list_path),
    )


if __name__ == "__main__":
    main()
