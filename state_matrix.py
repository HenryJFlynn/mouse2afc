import logging
import math
import numpy as np

from pybpodapi.protocol import Bpod
from pybpodapi.protocol import StateMachine
from pybpodapi.state_machine.state_machine_base import SMAError

from definitions.constant import Constant as Const
from definitions.draw_stim_type import DrawStimType
from definitions.experiment import ExperimentType
from definitions.incorrect_choice_signal_type import IncorrectChoiceSignalType
from definitions.iti_signal_type import ITISignalType
from definitions.matrix_state import MatrixState
from definitions.visual_stim_angle import VisualStimAngle

from utils import EncTrig
from utils import GetValveTimes
from utils import floor
from utils import iff
from utils import mod
from utils import round

logger = logging.getLogger(__name__)

# TODO: Properly pass emulator mode
EMULATOR_MODE = True

PORT_STR = 'Port'
PWM_STR = 'PWM'
OUT_STR = 'Out'
IN_STR = 'In'

DEFAULT_WIRE_TTL_DURATION = 0.02
DEFAULT_LED_ERROR_RATE = 0.1


class PluginSerialPorts:
    pass


class StateMatrixError(SMAError):
    pass


def error(message):
    logger.error(message)
    raise StateMatrixError(message)


def fwrite(port, millis, type):
    pass


def pwm_str(port):
    return f'{PWM_STR}{str(port)}'


def port_str(port, out=False):
    return f'{PORT_STR}{str(port)}{OUT_STR if out else IN_STR}'


class StateMatrix(StateMachine):
    def __init__(self, bpod, task_parameters, data, i_trial):
        super().__init__(bpod)
        # Define ports
        lmr_air_ports = task_parameters.GUI.Ports_LMRAir
        LeftPort = floor(mod(lmr_air_ports / 1000, 10))
        CenterPort = floor(mod(lmr_air_ports / 100, 10))
        RightPort = floor(mod(lmr_air_ports / 10, 10))
        AirSolenoid = mod(task_parameters.GUI.Ports_LMRAir, 10)
        LeftPortOut = port_str(LeftPort, out=True)
        CenterPortOut = port_str(CenterPort, out=True)
        RightPortOut = port_str(RightPort, out=True)
        LeftPortIn = port_str(LeftPort)
        CenterPortIn = port_str(CenterPort)
        RightPortIn = port_str(RightPort)

        # Duration of the TTL signal to denote start and end of trial for 2P
        WireTTLDuration = DEFAULT_WIRE_TTL_DURATION

        # PWM = (255 * (100-Attenuation))/100
        LeftPWM = round((100 - task_parameters.GUI.LeftPokeAttenPrcnt) * 2.55)
        CenterPWM = round(
            (100 - task_parameters.GUI.CenterPokeAttenPrcnt) * 2.55)
        RightPWM = round(
            (100 - task_parameters.GUI.RightPokeAttenPrcnt) * 2.55)

        LEDErrorRate = DEFAULT_LED_ERROR_RATE

        IsLeftRewarded = data.Custom.LeftRewarded[i_trial]

        if task_parameters.GUI.ExperimentType == ExperimentType.Auditory:
            DeliverStimulus = [('BNCState', 1)]
            ContDeliverStimulus = []
            StopStimulus = iff(
                task_parameters.GUI.StimAfterPokeOut, [], [('BNCState', 0)])
            ChoiceStopStimulus = iff(
                task_parameters.GUI.StimAfterPokeOut, [('BNCState', 0)], [])
            EWDStopStimulus = [('BNCState', 0)]
        elif task_parameters.GUI.ExperimentType == \
                ExperimentType.LightIntensity:
            # Divide Intensity by 100 to get fraction value
            LeftPWMStim = round(
                data.Custom.LightIntensityLeft[i_trial] * LeftPWM / 100)
            RightPWMStim = round(
                data.Custom.LightIntensityRight[
                    i_trial] * RightPWM / 100)
            DeliverStimulus = [
                (pwm_str(LeftPort), LeftPWMStim),
                (pwm_str(RightPort), RightPWMStim)
            ]
            ContDeliverStimulus = DeliverStimulus
            StopStimulus = iff(
                task_parameters.GUI.StimAfterPokeOut, DeliverStimulus, [])
            ChoiceStopStimulus = []
            EWDStopStimulus = []
        elif task_parameters.GUI.ExperimentType == \
                ExperimentType.GratingOrientation:
            rightPortAngle = VisualStimAngle.get_degrees(
                task_parameters.GUI.VisualStimAnglePortRight)
            leftPortAngle = VisualStimAngle.get_degrees(
                task_parameters.GUI.VisualStimAnglePortLeft)
            # Calculate the distance between right and left port angle to
            # determine whether we should use the circle arc between the two
            # values in the clock-wise or counter-clock-wise direction to
            # calculate the different difficulties.
            ccw = iff(mod(rightPortAngle - leftPortAngle, 360) < mod(
                leftPortAngle - rightPortAngle, 360), True, False)
            if ccw:
                finalDV = data.Custom.DV[i_trial]
                if rightPortAngle < leftPortAngle:
                    rightPortAngle += 360
                angleDiff = rightPortAngle - leftPortAngle
                minAngle = leftPortAngle
            else:
                finalDV = -data.Custom.DV[i_trial]
                if leftPortAngle < rightPortAngle:
                    leftPortAngle += 360
                angleDiff = leftPortAngle - rightPortAngle
                minAngle = rightPortAngle
            # orientation = ((DVMax - DV)*(DVMAX-DVMin)*(
            #   MaxAngle - MinANgle)) + MinAngle
            gratingOrientation = ((1 - finalDV) * angleDiff / 2) + minAngle
            gratingOrientation = mod(gratingOrientation, 360)
            data.Custom.drawParams.stimType = DrawStimType.StaticGratings
            data.Custom.drawParams.gratingOrientation = gratingOrientation
            data.Custom.drawParams.numCycles = task_parameters.GUI.numCycles
            data.Custom.drawParams.cyclesPerSecondDrift = \
                task_parameters.GUI.cyclesPerSecondDrift
            data.Custom.drawParams.phase = task_parameters.GUI.phase
            data.Custom.drawParams.gaborSizeFactor = \
                task_parameters.GUI.gaborSizeFactor
            data.Custom.drawParams.gaussianFilterRatio = \
                task_parameters.GUI.gaussianFilterRatio
            # Start from the 5th byte
            # serializeAndWrite(data.dotsMapped_file, 5,
            #                   data.Custom.drawParams)
            # data.dotsMapped_file.data(1: 4) = typecast(uint32(1), 'uint8');

            DeliverStimulus = [('SoftCode', 5)]
            ContDeliverStimulus = []
            StopStimulus = iff(
                task_parameters.GUI.StimAfterPokeOut, [], [('SoftCode', 6)])
            ChoiceStopStimulus = iff(
                task_parameters.GUI.StimAfterPokeOut, [('SoftCode', 6)], [])
            EWDStopStimulus = [('SoftCode', 6)]
        elif task_parameters.GUI.ExperimentType == ExperimentType.RandomDots:
            # Setup the parameters
            # Use 20% of the screen size. Assume apertureSize is the diameter
            task_parameters.GUI.circleArea = math.pi * \
                ((task_parameters.GUI.apertureSizeWidth / 2) ** 2)
            task_parameters.GUI.nDots = round(
                task_parameters.GUI.circleArea * task_parameters.GUI.drawRatio)

            data.Custom.drawParams.stimType = DrawStimType.RDK
            data.Custom.drawParams.centerX = task_parameters.GUI.centerX
            data.Custom.drawParams.centerY = task_parameters.GUI.centerY
            data.Custom.drawParams.apertureSizeWidth = \
                task_parameters.GUI.apertureSizeWidth
            data.Custom.drawParams.apertureSizeHeight = \
                task_parameters.GUI.apertureSizeHeight
            data.Custom.drawParams.drawRatio = task_parameters.GUI.drawRatio
            data.Custom.drawParams.mainDirection = floor(
                VisualStimAngle.get_degrees(
                    iff(IsLeftRewarded,
                        task_parameters.GUI.VisualStimAnglePortLeft,
                        task_parameters.GUI.VisualStimAnglePortRight)))
            data.Custom.drawParams.dotSpeed = \
                task_parameters.GUI.dotSpeedDegsPerSec
            data.Custom.drawParams.dotLifetimeSecs = \
                task_parameters.GUI.dotLifetimeSecs
            data.Custom.drawParams.coherence = data.Custom.DotsCoherence[
                i_trial]
            data.Custom.drawParams.screenWidthCm = \
                task_parameters.GUI.screenWidthCm
            data.Custom.drawParams.screenDistCm = \
                task_parameters.GUI.screenDistCm
            data.Custom.drawParams.dotSizeInDegs = \
                task_parameters.GUI.dotSizeInDegs

            # Start from the 5th byte
            # serializeAndWrite(data.dotsMapped_file, 5,
            #                   data.Custom.drawParams)
            # data.dotsMapped_file.data(1: 4) = \
            #   typecast(uint32(1), 'uint8');

            DeliverStimulus = [('SoftCode', 5)]
            ContDeliverStimulus = []
            StopStimulus = iff(
                task_parameters.GUI.StimAfterPokeOut, [], [('SoftCode', 6)])
            ChoiceStopStimulus = iff(
                task_parameters.GUI.StimAfterPokeOut, [('SoftCode', 6)], [])
            EWDStopStimulus = [('SoftCode', 6)]
        else:
            error('Unexpected ExperimentType')

        # Valve opening is a bitmap. Open each valve separately by raising 2 to
        # the power of port number - 1
        # LeftValve = 2 ** (LeftPort - 1)
        # CenterValve = 2 ** (CenterPort - 1)
        # RightValve = 2 ** (RightPort - 1)
        # AirSolenoidOn = 2 ** (AirSolenoid - 1)
        LeftValve = LeftPort
        CenterValve = CenterPort
        RightValve = RightPort
        AirSolenoidOn = AirSolenoid

        LeftValveTime = GetValveTimes(
            data.Custom.RewardMagnitude[i_trial][0], LeftPort)
        CenterValveTime = GetValveTimes(
            data.Custom.CenterPortRewAmount[i_trial], CenterPort)
        RightValveTime = GetValveTimes(
            data.Custom.RewardMagnitude[i_trial][1], RightPort)

        RewardedPort = iff(IsLeftRewarded, LeftPort, RightPort)
        RewardedPortPWM = iff(IsLeftRewarded, LeftPWM, RightPWM)
        IncorrectConsequence = iff(
            not task_parameters.GUI.HabituateIgnoreIncorrect,
            str(MatrixState.WaitForPunishStart),
            str(MatrixState.RegisterWrongWaitCorrect))
        LeftActionState = iff(IsLeftRewarded, str(
            MatrixState.WaitForRewardStart), IncorrectConsequence)
        RightActionState = iff(IsLeftRewarded, IncorrectConsequence, str(
            MatrixState.WaitForRewardStart))
        RewardIn = iff(IsLeftRewarded, LeftPortIn, RightPortIn)
        RewardOut = iff(IsLeftRewarded, LeftPortOut, RightPortOut)
        PunishIn = iff(IsLeftRewarded, RightPortIn, LeftPortIn)
        PunishOut = iff(IsLeftRewarded, RightPortOut, LeftPortOut)
        ValveTime = iff(IsLeftRewarded, LeftValveTime, RightValveTime)
        ValveCode = iff(IsLeftRewarded, LeftValve, RightValve)

        ValveOrWireSolenoid = 'Valve'
        if task_parameters.GUI.CutAirStimDelay and \
                task_parameters.GUI.CutAirSampling:
            AirFlowStimDelayOff = [(ValveOrWireSolenoid, AirSolenoidOn)]
            # AirFlowStimDelayOn = []
            AirFlowSamplingOff = [(ValveOrWireSolenoid, AirSolenoidOn)]
            # Must set it on again
            AirFlowSamplingOn = []
        elif task_parameters.GUI.CutAirStimDelay:
            AirFlowStimDelayOff = [(ValveOrWireSolenoid, AirSolenoidOn)]
            # AirFlowStimDelayOn = [(ValveOrWireSolenoid, AirSolenoidOff)]
            AirFlowSamplingOff = []
            AirFlowSamplingOn = []
        elif task_parameters.GUI.CutAirSampling:
            AirFlowStimDelayOff = []
            # AirFlowStimDelayOn = []
            AirFlowSamplingOff = [(ValveOrWireSolenoid, AirSolenoidOn)]
            AirFlowSamplingOn = []
        else:
            AirFlowStimDelayOff = []
            # AirFlowStimDelayOn = []
            AirFlowSamplingOff = []
            AirFlowSamplingOn = []

        if task_parameters.GUI.CutAirReward:
            AirFlowRewardOff = [('Valve', AirSolenoidOn)]
        else:
            AirFlowRewardOff = []
        AirFlowRewardOn = []

        # Check if to play beep at end of minimum sampling
        MinSampleBeep = iff(task_parameters.GUI.BeepAfterMinSampling, [
                            ('SoftCode', 12)], [])
        MinSampleBeepDuration = iff(
            task_parameters.GUI.BeepAfterMinSampling, 0.01, 0)
        # GUI option RewardAfterMinSampling
        # If center - reward is enabled, then a reward is given once MinSample
        # is over and no further sampling is given.
        RewardCenterPort = iff(task_parameters.GUI.RewardAfterMinSampling,
                               [('Valve', CenterValve)] + StopStimulus,
                               ContDeliverStimulus)
        Timer_CPRD = iff(
            task_parameters.GUI.RewardAfterMinSampling, CenterValveTime,
            task_parameters.GUI.StimulusTime - task_parameters.GUI.MinSample)

        # White Noise played as Error Feedback
        ErrorFeedback = iff(task_parameters.GUI.PlayNoiseforError, [(
            'SoftCode', 11)], [])

        # CatchTrial
        FeedbackDelayCorrect = iff(data.Custom.CatchTrial[
            i_trial], Const.FEEDBACK_CATCH_CORRECT_SEC,
            task_parameters.GUI.FeedbackDelay)

        # GUI option CatchError
        FeedbackDelayError = iff(task_parameters.GUI.CatchError,
                                 Const.FEEDBACK_CATCH_INCORRECT_SEC,
                                 task_parameters.GUI.FeedbackDelay)
        SkippedFeedbackSignal = iff(
            task_parameters.GUI.CatchError, [], ErrorFeedback)

        # Incorrect Choice signal
        if task_parameters.GUI.IncorrectChoiceSignalType == \
                IncorrectChoiceSignalType.NoisePulsePal:
            PunishmentDuration = 0.01
            IncorrectChoice_Signal = [('SoftCode', 11)]
        elif task_parameters.GUI.IncorrectChoiceSignalType == \
                IncorrectChoiceSignalType.BeepOnWire_1:
            PunishmentDuration = 0.25
            IncorrectChoice_Signal = [('Wire1', 1)]
        elif task_parameters.GUI.IncorrectChoiceSignalType == \
                IncorrectChoiceSignalType.PortLED:
            PunishmentDuration = 0.1
            IncorrectChoice_Signal = [
                (pwm_str(LeftPort), LeftPWM),
                (pwm_str(CenterPort), CenterPWM),
                (pwm_str(RightPort), RightPWM)
            ]
        elif task_parameters.GUI.IncorrectChoiceSignalType == \
                IncorrectChoiceSignalType.None_:
            PunishmentDuration = 0.01
            IncorrectChoice_Signal = []
        else:
            error('Unexpected IncorrectChoiceSignalType value')

        # ITI signal
        if task_parameters.GUI.ITISignalType == ITISignalType.Beep:
            ITI_Signal_Duration = 0.01
            ITI_Signal = [('SoftCode', 12)]
        elif task_parameters.GUI.ITISignalType == ITISignalType.PortLED:
            ITI_Signal_Duration = 0.1
            ITI_Signal = [
                (pwm_str(LeftPort), LeftPWM),
                (pwm_str(CenterPort), CenterPWM),
                (pwm_str(RightPort), RightPWM)
            ]
        elif task_parameters.GUI.ITISignalType == ITISignalType.None_:
            ITI_Signal_Duration = 0.01
            ITI_Signal = []
        else:
            error('Unexpected ITISignalType value')

        # Wire1 settings
        Wire1OutError = iff(task_parameters.GUI.Wire1VideoTrigger, [(
                            'Wire2', 2)], [])
        Wire1OutCorrectCondition = task_parameters.GUI.Wire1VideoTrigger and \
            data.Custom.CatchTrial[i_trial]
        Wire1OutCorrect = iff(Wire1OutCorrectCondition,
                              [('Wire2', 2)], [])

        # LED on the side lateral port to cue the rewarded side at the
        # beginning of the training. On auditory discrimination task, both
        # lateral ports are illuminated after end of stimulus delivery.
        if data.Custom.ForcedLEDTrial[i_trial]:
            ExtendedStimulus = [(pwm_str(RewardedPort), RewardedPortPWM)]
        elif task_parameters.GUI.ExperimentType == ExperimentType.Auditory:
            ExtendedStimulus = [
                (pwm_str(LeftPort), LeftPWM),
                (pwm_str(RightPort), RightPWM)
            ]
        else:
            ExtendedStimulus = []

        # Softcode handler for i_trial == 1 in HomeCage
        # to close training chamber door
        CloseChamber = iff(i_trial == 1 and data.Custom.IsHomeCage,
                           [('SoftCode', 30)], [])

        PCTimeout = task_parameters.GUI.PCTimeout
        # Build state matrix
        self.set_global_timer(1, FeedbackDelayCorrect)
        self.set_global_timer(2, FeedbackDelayError)
        self.set_global_timer(3, iff(
            task_parameters.GUI.TimeOutEarlyWithdrawal,
            task_parameters.GUI.TimeOutEarlyWithdrawal,
            0.01))
        self.set_global_timer(4, task_parameters.GUI.ChoiceDeadLine)
        self.add_state(state_name=str(MatrixState.ITI_Signal),
                       state_timer=ITI_Signal_Duration,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.WaitForCenterPoke)},
            output_actions=ITI_Signal)
        self.add_state(state_name=str(MatrixState.WaitForCenterPoke),
                       state_timer=0,
                       state_change_conditions={
            CenterPortIn: str(MatrixState.PreStimReward)},
            output_actions=[(pwm_str(CenterPort), CenterPWM)])
        PreStimRewardStateTimer = iff(
            task_parameters.GUI.PreStimuDelayCntrReward,
            GetValveTimes(task_parameters.GUI.PreStimuDelayCntrReward,
                          CenterPort), 0.01)
        self.add_state(state_name=str(MatrixState.PreStimReward),
                       state_timer=PreStimRewardStateTimer,
                       state_change_conditions={Bpod.Events.Tup: str(
                           MatrixState.TriggerWaitForStimulus)},
                       output_actions=iff(
            task_parameters.GUI.PreStimuDelayCntrReward,
            [('Valve', CenterValve)], [])
        )
        # The next method is useful to close the 2 - photon shutter. It is
        # enabled by setting Optogenetics StartState to this state and end
        # state to ITI.
        self.add_state(state_name=str(MatrixState.TriggerWaitForStimulus),
                       state_timer=WireTTLDuration,
                       state_change_conditions={
            CenterPortOut: str(MatrixState.StimDelayGrace),
            Bpod.Events.Tup: str(MatrixState.WaitForStimulus)},
            output_actions=(CloseChamber + AirFlowStimDelayOff))
        self.add_state(state_name=str(MatrixState.WaitForStimulus),
                       state_timer=max(
            0, task_parameters.GUI.StimDelay - WireTTLDuration),
            state_change_conditions={
            CenterPortOut: str(MatrixState.StimDelayGrace),
            Bpod.Events.Tup: str(MatrixState.stimulus_delivery)},
            output_actions=AirFlowStimDelayOff)
        self.add_state(state_name=str(MatrixState.StimDelayGrace),
                       state_timer=task_parameters.GUI.StimDelayGrace,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.broke_fixation),
            CenterPortIn: str(MatrixState.TriggerWaitForStimulus)},
            output_actions=AirFlowStimDelayOff)
        self.add_state(state_name=str(MatrixState.broke_fixation),
                       state_timer=iff(
            not PCTimeout, task_parameters.GUI.TimeOutBrokeFixation,
            0.01),
            state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.ITI)},
            output_actions=ErrorFeedback)
        self.add_state(state_name=str(MatrixState.stimulus_delivery),
                       state_timer=task_parameters.GUI.MinSample,
                       state_change_conditions={
            CenterPortOut: str(MatrixState.early_withdrawal),
            Bpod.Events.Tup: str(MatrixState.BeepMinSampling)},
            output_actions=(DeliverStimulus + AirFlowSamplingOff))
        self.add_state(state_name=str(MatrixState.early_withdrawal),
                       state_timer=0,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.timeOut_EarlyWithdrawal
                                 )},
                       output_actions=(EWDStopStimulus + AirFlowSamplingOn + [
                           ('GlobalTimerTrig', EncTrig(3))]))
        self.add_state(state_name=str(MatrixState.BeepMinSampling),
                       state_timer=MinSampleBeepDuration,
                       state_change_conditions={
            CenterPortOut: str(MatrixState.TriggerWaitChoiceTimer),
            Bpod.Events.Tup: str(MatrixState.CenterPortRewardDelivery
                                 )},
                       output_actions=(ContDeliverStimulus + MinSampleBeep))
        self.add_state(state_name=str(MatrixState.CenterPortRewardDelivery),
                       state_timer=Timer_CPRD,
                       state_change_conditions={
            CenterPortOut: str(MatrixState.TriggerWaitChoiceTimer),
            Bpod.Events.Tup: str(MatrixState.WaitCenterPortOut)},
            output_actions=RewardCenterPort)
        # TODO: Stop stimulus is fired twice in case of center reward and then
        # wait for choice. Fix it such that it'll be always fired once.
        self.add_state(state_name=str(MatrixState.TriggerWaitChoiceTimer),
                       state_timer=0,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.WaitForChoice)},
            output_actions=(StopStimulus + ExtendedStimulus + [
                ('GlobalTimerTrig', EncTrig(4))]))
        self.add_state(state_name=str(MatrixState.WaitCenterPortOut),
                       state_timer=0,
                       state_change_conditions={
            CenterPortOut: str(MatrixState.WaitForChoice),
            LeftPortIn: LeftActionState,
            RightPortIn: RightActionState,
            'GlobalTimer4_End': str(MatrixState.timeOut_missed_choice
                                    )},
                       output_actions=(StopStimulus + ExtendedStimulus + [
                                      ('GlobalTimerTrig', EncTrig(4))]))
        self.add_state(state_name=str(MatrixState.WaitForChoice),
                       state_timer=0,
                       state_change_conditions={
            LeftPortIn: LeftActionState,
            RightPortIn: RightActionState,
            'GlobalTimer4_End': str(MatrixState.timeOut_missed_choice
                                    )},
                       output_actions=(StopStimulus + ExtendedStimulus))
        self.add_state(state_name=str(MatrixState.WaitForRewardStart),
                       state_timer=0,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.WaitForReward)},
            output_actions=(Wire1OutCorrect + ChoiceStopStimulus + [
                ('GlobalTimerTrig', EncTrig(1))]))
        self.add_state(state_name=str(MatrixState.WaitForReward),
                       state_timer=FeedbackDelayCorrect,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.Reward),
            'GlobalTimer1_End': str(MatrixState.Reward),
            RewardOut: str(MatrixState.RewardGrace)},
            output_actions=AirFlowRewardOff)
        self.add_state(state_name=str(MatrixState.RewardGrace),
                       state_timer=task_parameters.GUI.FeedbackDelayGrace,
                       state_change_conditions={
            RewardIn: str(MatrixState.WaitForReward),
            Bpod.Events.Tup: str(MatrixState.timeOut_SkippedFeedback
                                 ),
            'GlobalTimer1_End': str(
                MatrixState.timeOut_SkippedFeedback),
            CenterPortIn: str(MatrixState.timeOut_SkippedFeedback),
            PunishIn: str(MatrixState.timeOut_SkippedFeedback)},
            output_actions=AirFlowRewardOn)
        self.add_state(state_name=str(MatrixState.Reward),
                       state_timer=ValveTime,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.WaitRewardOut)},
            output_actions=[('Valve', ValveCode)])
        self.add_state(state_name=str(MatrixState.WaitRewardOut),
                       state_timer=1,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.ITI),
            RewardOut: str(MatrixState.ITI)},
            output_actions=[])
        self.add_state(state_name=str(MatrixState.RegisterWrongWaitCorrect),
                       state_timer=0,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.WaitForChoice)},
            output_actions=[])
        self.add_state(state_name=str(MatrixState.WaitForPunishStart),
                       state_timer=0,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.WaitForPunish)},
            output_actions=(Wire1OutError + ChoiceStopStimulus + [
                           ('GlobalTimerTrig', EncTrig(2))]))
        self.add_state(state_name=str(MatrixState.WaitForPunish),
                       state_timer=FeedbackDelayError,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.Punishment),
            'GlobalTimer2_End': str(MatrixState.Punishment),
            PunishOut: str(MatrixState.PunishGrace)},
            output_actions=AirFlowRewardOff)
        self.add_state(state_name=str(MatrixState.PunishGrace),
                       state_timer=task_parameters.GUI.FeedbackDelayGrace,
                       state_change_conditions={
            PunishIn: str(MatrixState.WaitForPunish),
            Bpod.Events.Tup: str(MatrixState.timeOut_SkippedFeedback
                                 ),
            'GlobalTimer2_End': str(
                MatrixState.timeOut_SkippedFeedback),
            CenterPortIn: str(MatrixState.timeOut_SkippedFeedback),
            RewardIn: str(MatrixState.timeOut_SkippedFeedback)},
            output_actions=[])
        self.add_state(state_name=str(MatrixState.Punishment),
                       state_timer=PunishmentDuration,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.timeOut_IncorrectChoice
                                 )},
                       output_actions=(
            IncorrectChoice_Signal + AirFlowRewardOn))
        self.add_state(state_name=str(MatrixState.timeOut_EarlyWithdrawal),
                       state_timer=LEDErrorRate,
                       state_change_conditions={
            'GlobalTimer3_End': str(MatrixState.ITI),
            Bpod.Events.Tup: str(
                MatrixState.timeOut_EarlyWithdrawalFlashOn)},
            output_actions=ErrorFeedback)
        self.add_state(state_name=str(
            MatrixState.timeOut_EarlyWithdrawalFlashOn),
            state_timer=LEDErrorRate,
            state_change_conditions={
            'GlobalTimer3_End': str(MatrixState.ITI),
            Bpod.Events.Tup: str(MatrixState.timeOut_EarlyWithdrawal
                                 )},
            output_actions=(ErrorFeedback + [(pwm_str(LeftPort), LeftPWM),
                                             (pwm_str(RightPort), RightPWM)]))
        self.add_state(state_name=str(MatrixState.timeOut_IncorrectChoice),
                       state_timer=iff(
            not PCTimeout,
            task_parameters.GUI.TimeOutIncorrectChoice,
            0.01),
            state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.ITI)},
            output_actions=[])
        self.add_state(state_name=str(MatrixState.timeOut_SkippedFeedback),
                       state_timer=(
            iff(not PCTimeout,
                task_parameters.GUI.TimeOutSkippedFeedback,
                0.01)),
            state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.ITI)},
            # TODO: See how to get around this if PCTimeout
            output_actions=SkippedFeedbackSignal)
        self.add_state(state_name=str(MatrixState.timeOut_missed_choice),
                       state_timer=iff(not PCTimeout,
                                       task_parameters.GUI.TimeOutMissedChoice,
                                       0.01),
                       state_change_conditions={
                           Bpod.Events.Tup: str(MatrixState.ITI)},
                       output_actions=(ErrorFeedback + ChoiceStopStimulus))
        self.add_state(state_name=str(MatrixState.ITI),
                       state_timer=WireTTLDuration,
                       state_change_conditions={
            Bpod.Events.Tup: str(MatrixState.ext_ITI)},
            output_actions=AirFlowRewardOn)
        self.add_state(state_name=str(MatrixState.ext_ITI),
                       state_timer=iff(
            not PCTimeout, task_parameters.GUI.ITI, 0.01),
            state_change_conditions={Bpod.Events.Tup: 'exit'},
            output_actions=AirFlowRewardOn)

        # If Optogenetics/2-Photon is enabled for a particular state, then we
        # modify that gien state such that it would send a signal to arduino
        # with the required offset delay to trigger the optogentics box.
        # Note: To precisely track your optogentics signal, split the arduino
        # output to the optogentics box and feed it as an input to Bpod input
        # TTL, e.g Wire1. This way, the optogentics signal gets written as
        # part of your data file. Don't forget to activate that input in the
        # Bpod main config.

        if data.Custom.OptoEnabled[i_trial]:
            # Convert seconds to millis as we will send ints to Arduino
            OptoDelay = np.array(
                [task_parameters.GUI.OptoStartDelay * 1000], dtype=np.uint32)
            OptoDelay = OptoDelay.view(np.uint8)
            OptoTime = np.array(
                [task_parameters.GUI.OptoMaxTime * 1000], dtype=np.uint32)
            OptoTime = OptoTime.view(np.uint8)
            if not EMULATOR_MODE or hasattr(PluginSerialPorts, 'OptoSerial'):
                fwrite(PluginSerialPorts.OptoSerial, OptoDelay, 'int8')
                fwrite(PluginSerialPorts.OptoSerial, OptoTime, 'int8')
            OptoStartEventIdx = \
                self.hardware.channels.output_channel_names.index('Wire3')
            OptoStopEventIdx = \
                self.hardware.channels.output_channel_names.index('Wire4')
            tuples = [
                (str(task_parameters.GUI.OptoStartState1), OptoStartEventIdx),
                (str(task_parameters.GUI.OptoEndState1), OptoStopEventIdx),
                (str(task_parameters.GUI.OptoEndState2), OptoStopEventIdx),
                (str(MatrixState.ext_ITI), OptoStopEventIdx)
            ]
            for state_name, event_idx in tuples:
                TrgtStateNum = self.state_names.index(state_name)
                self.output_matrix[TrgtStateNum][event_idx] = 1
