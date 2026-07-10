add_executable(sample-a2f-blendshape-export)

set_target_properties(sample-a2f-blendshape-export PROPERTIES
    ARCHIVE_OUTPUT_DIRECTORY ${A2F_BINARY_DIR}/lib
    LIBRARY_OUTPUT_DIRECTORY ${A2F_BINARY_DIR}/lib
    RUNTIME_OUTPUT_DIRECTORY ${A2F_BINARY_DIR}/bin
    VS_DEBUGGER_ENVIRONMENT "PATH=%PATH%;${TENSORRT_ROOT_DIR}/lib;${CUDA_PATH}/bin;$<TARGET_FILE_DIR:audio2x>"
    VS_DEBUGGER_WORKING_DIRECTORY ${AUDIO2X_SDK_ROOT}
)

target_sources(sample-a2f-blendshape-export PRIVATE
    main.cpp
)

target_compile_definitions(sample-a2f-blendshape-export PRIVATE
    TEST_DATA_DIR="${TEST_DATA_DIR}"
)

target_link_libraries(sample-a2f-blendshape-export PRIVATE
    audio2x
    AudioFile::AudioFile
    TensorRT::TensorRT   # resolve libaudio2x.so's undefined TRT symbols at final link
    CUDA::cudart
)
