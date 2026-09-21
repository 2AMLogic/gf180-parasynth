module test_decimate_2x_tm_sym_saturation;
    reg clk = 0;
    reg rst_n = 0;
    reg in_valid = 0;
    reg signed [15:0] in_sample = 0;
    wire out_valid;
    wire signed [15:0] out_sample;
    reg expect_positive;
    reg saw_rail;
    integer i;

    decimate_2x_tm_sym dut(.*);
    always #5 clk = ~clk;

    always @(negedge clk) begin
        if (rst_n && out_valid) begin
            if (expect_positive && out_sample == 16'sd32767) saw_rail = 1;
            if (!expect_positive && out_sample == -16'sd32768) saw_rail = 1;
        end
    end

    task send_sample(input signed [15:0] value);
        begin
            @(negedge clk);
            while (dut.busy) @(negedge clk);
            in_sample = value;
            in_valid = 1;
            @(negedge clk);
            in_valid = 0;
        end
    endtask

    task reset_filter;
        begin
            rst_n = 0;
            in_valid = 0;
            repeat (2) @(negedge clk);
            rst_n = 1;
            repeat (1) @(negedge clk);
        end
    endtask

    initial begin
        saw_rail = 0;
        expect_positive = 1;
        reset_filter();
        for (i = 0; i < 80; i = i + 1)
            send_sample((i >= 20 && i < 40) ? 16'sd32767 : 16'sd0);
        repeat (20) @(negedge clk);
        if (!saw_rail) $fatal(1, "positive FIR ringing did not saturate");

        saw_rail = 0;
        expect_positive = 0;
        reset_filter();
        for (i = 0; i < 80; i = i + 1)
            send_sample((i >= 20 && i < 40) ? -16'sd32768 : 16'sd0);
        repeat (20) @(negedge clk);
        if (!saw_rail) $fatal(1, "negative FIR ringing did not saturate");

        $display("PASS decimate_2x_tm_sym saturates both Q1.15 rails");
        $finish;
    end
endmodule
