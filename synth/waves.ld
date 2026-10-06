ENTRY(wave_output)
SECTIONS {
    . = 0x8353e400;
    .text : { KEEP(*(.text.wave_eval)) KEEP(*(.text.wave_name)) *(.text*) }
    .rodata : ALIGN(16) { *(.rodata*) }
    .data : ALIGN(16) { *(.data*) }
    .bss : ALIGN(16) { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }
}
